import { access, lstat, readFile, realpath, rename, stat, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { isAbsolute, join, relative, resolve } from "node:path";
import {
	z, type ExtensionAPI, type ExtensionContext,
	type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent,
} from "@oh-my-pi/pi-coding-agent";
import { lookup } from "@oh-my-pi/pi-coding-agent/config/registry";
import {
	configRoot as defaultConfigRoot, GATE_FILE, type Gate, offText, ownerOnly, readGateText, recordDir, resolveRecord,
} from "../../lib/repo-record.ts";
import {
	SUBPROCESS_TIMEOUT_MS, stringList, mappingSchema, detailsSchema, policyEvent, absolute, repoRoot,
	paths, matches, missing, sentence, readReason, gitActions, readRecord, note, absentFile, fileErrorSchema,
	recordRead, type GitAction, type Mapping, type RepoRecord, type State as SharedState, type ToolEvent,
} from "../../lib/rails-runtime.ts";

const prCiSchema = z.object({
	headRefOid: z.string().regex(/^[0-9a-f]{40}$/),
	url: z.string().regex(/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/\d+$/),
	statusCheckRollup: z.array(z.union([
		z.object({ __typename: z.literal("CheckRun"), name: z.string(), status: z.string(), conclusion: z.union([z.string(), z.literal(null)]) })
			.transform(check => checkRunReport(check.name, check.status, check.conclusion)),
		z.object({ __typename: z.literal("StatusContext"), context: z.string(), state: z.string() })
			.transform(check => statusContextReport(check.context, check.state)),
	])),
});

// Harness variables the omp runtime sets for its own commands. Rails strip them from every repo
// command they run (path check, checks.commit, harness checks) so repos never see the harness.
const harnessVariables = ["AGENT"];
// Characters of a failed commit check's output shown to the agent (the tail, where findings end).
const commitOutputLimit = 8000;
// Derived from this file's location so the rails hold no machine path: <omp>/scripts/ci_runner.py.
const ciRunnerPath = fileURLToPath(new URL("../../../scripts/ci_runner.py", import.meta.url));
function shellWord(value: string): string {
	return /^[\w@%+=:,./-]+$/.test(value) ? value : `'${value.replaceAll("'", "'\\''")}'`;
}

const PR_FLAGS = ["--auto", "--admin", "--delete-branch", "-d", "--squash", "-s", "--merge", "-m", "--rebase", "-r", "--help"];
function prSelector(args: string[]): string | undefined {
	for (let i = 2; i < args.length; i++) {
		const arg = args[i] ?? "";
		if (arg === "--") return args[i + 1];
		if (!arg.startsWith("-")) return arg;
		if (!arg.includes("=") && !PR_FLAGS.includes(arg)) i++;
	}
	return undefined;
}
type CheckReport = { name: string; state: string; green: boolean; acceptable: boolean };
function checkRunReport(name: string, status: string, conclusion: string | null): CheckReport {
	return {
		name, state: `${status}/${conclusion ?? ""}`,
		green: status === "COMPLETED" && conclusion === "SUCCESS",
		acceptable: status === "COMPLETED" && ["SUCCESS", "NEUTRAL", "SKIPPED"].includes(conclusion ?? ""),
	};
}
function statusContextReport(context: string, state: string): CheckReport {
	return { name: context, state, green: state === "SUCCESS", acceptable: state === "SUCCESS" };
}
function porcelainAdditions(stdout: string): Set<string> {
	const added = new Set<string>();
	const records = stdout.split("\0");
	for (let index = 0; index < records.length; index++) {
		const status = (records[index] ?? "").slice(0, 2);
		if (!status) continue;
		if (status === "??" || /[AR]/.test(status)) added.add((records[index] ?? "").slice(3));
		if (status.includes("R") || status.includes("C")) index++;
	}
	return added;
}
async function canonicalCandidates(root: string, canonicalRoot: string, changed: string[]): Promise<string[]> {
	const candidates: string[] = [];
	for (const path of changed) {
		try {
			await lstat(path);
			const canonical = await realpath(path);
			const rel = relative(canonicalRoot, canonical);
			candidates.push(rel === ".." || rel.startsWith("../") || isAbsolute(rel)
				? resolve(canonicalRoot, relative(root, path)) : canonical);
		} catch (error) {
			const parsed = fileErrorSchema.safeParse(error);
			if (!parsed.success || parsed.data.code !== "ENOENT") throw error;
		}
	}
	return [...new Set(candidates)];
}
async function setDynaflows(dir: string, enabled: boolean): Promise<string> {
	const path = join(dir, GATE_FILE);
	const gate = z.record(z.string(), z.unknown()).parse(JSON.parse(await readGateText(dir)));
	gate["dynaflows"] = { enabled };
	const temp = `${path}.${randomUUID()}.tmp`;
	await writeFile(temp, `${JSON.stringify(gate, null, 2)}\n`);
	await rename(temp, path);
	return path;
}
function matchedHead(args: string[]): string | undefined {
	const at = args.findIndex(arg => arg === "--match-head-commit" || arg.startsWith("--match-head-commit="));
	if (at < 0) return undefined;
	const flag = args[at] ?? "";
	return flag.includes("=") ? flag.slice(flag.indexOf("=") + 1) : args[at + 1];
}
function headFormatReason(head: string | undefined): string | undefined {
	if (!head) return "Agent rails: merge requires --match-head-commit <40-hex verified SHA>";
	if (/[$`]/.test(head)) return `Agent rails: received unexpanded shell text \`${head}\`; rails check the literal command, so pass the 40-hex SHA itself`;
	if (!/^[0-9a-f]{40}$/.test(head)) return `Agent rails: --match-head-commit must be a lowercase 40-hex SHA, got ${head}`;
	return undefined;
}
type ExecApi = Pick<ExtensionAPI, "exec">;
type DynaflowsApi = Pick<ExtensionAPI, "on" | "exec" | "registerCommand" | "registerTool"> & { pi: Pick<ExtensionAPI["pi"], "settings"> };
type Applied = { notice: string; content?: string | undefined };
function missingRecordOutcome(notify: (notice: string) => string, slug: string, recordPath: string, enabled: boolean): Applied {
	if (!enabled) return { notice: notify(`Agent rails: ${slug} has no per-repo record, so dynaflows is already off.`) };
	return { notice: notify(`Agent rails: ${slug} has no per-repo record; starting repository setup.`), content:
		`The owner asked to enable dynaflows for ${slug}, which has no per-repo record yet. ` +
		"Start skill://dynaflows/references/harness-setup.md setup now, from Step 0. Write the record " +
		`yourself at ${recordPath} as setup reaches each file, with gate.json setting ` +
		'"dynaflows": {"enabled": true}, so dynaflows is on once the record is complete. Never ask the owner to save a file.' };
}
function resumeContent(slug: string, dir: string, reason: string): string {
	return `The owner asked to enable dynaflows for ${slug}, but its per-repo record ` +
		`${join(dir, GATE_FILE)} is refused: ${reason}. Resume skill://dynaflows/references/harness-setup.md setup now and repair ` +
		'the record yourself in place, with "dynaflows": {"enabled": true}. ' +
		"Never ask the owner to save a file.";
}
function startContent(slug: string, workflow: string): string {
	return `Dynaflows on for ${slug}. The owner asked to start dynaflows here, so begin now: read ` +
		`${workflow} and assess this repo against its Start here playbook selection ` +
		"(Project setup when controls, the Verification surface or the feature map are missing). Report the stage you found " +
		"and start it. If the owner's message also names a task, route that task; if setup is complete and no task was " +
		"named, report that dynaflows is ready and ask the owner for the task.";
}
const agentMessage = (content: string) => ({ message: { customType: "agent-rails-dynaflows", content, display: false, attribution: "agent" as const } });
const toolReply = (text: string) => ({ content: [{ type: "text" as const, text }], details: {} });
const dynaflowsSchema = z.object({ schemaVersion: z.literal(1), enabled: z.boolean() });
type Dynaflows = z.infer<typeof dynaflowsSchema>;
interface State extends SharedState {
	edits: Set<string>;
	pathFailures: Map<string, string[]>;
	pointed: boolean;
}
function fresh(): State {
	return {
		reads: new Set(), notes: new Set(), edits: new Set(), pathFailures: new Map(),
		pointed: false, proposal: [],
	};
}
// The harness checks: guardrails run from the harness's own reference project (never installed
// into the repo), over the whole repo, with the record as config. Rails append
// `--repo <worktree> --record <record dir>`.
const guardrailsProject = fileURLToPath(new URL("./engine", import.meta.url));
const defaultGuardrailsCommand = ["uv", "run", "--project", guardrailsProject, "guardrails", "check"];
const harnessPathCommand = ["uv", "run", "--project", guardrailsProject, "guardrails", "path-check"];
// The harness checks run only after checks.commit passed, so a harness-only finding can mean a repo
// checker already enforces that step and the record has not given the step to it.
const harnessOnlyNote =
	"checks.commit passed. If a repo checker already enforces a failing step's rules, do not reshape repo " +
	"code to the harness copy: route to project setup (skill://dynaflows/references/harness-setup.md, adopt mode) to " +
	"draft guardrails.check.repo for the owner to save.";

// The dynaflows directive is a whole prompt line, case-insensitive: `$dynaflows` alone, or with an
// on or off verb after it or before it. dynaflowsSetting gives true (on), false (off) or undefined.
// Asks inside a sentence are left to the main agent, which tells a request from a question or a
// quote and calls the dynaflows tool.
const dynaflowsOnVerbs = "on|enable|init|initiali[sz]e|start|setup|set up|turn on";
const dynaflowsDirective = new RegExp(
	`^(?:\\$dynaflows(?: (${dynaflowsOnVerbs}|off|disable|stop))?|(${dynaflowsOnVerbs}|disable|stop|turn off) \\$dynaflows)$`, "i");
function dynaflowsSetting(line: string): boolean | undefined {
	const match = dynaflowsDirective.exec(line.trim().replace(/\s+/g, " "));
	if (match) return !/^(?:off|disable|stop|turn off)$/i.test(match[1] ?? match[2] ?? "");
	return undefined;
}

// Dynaflows is on for a repo only when its per-repo record (resolved from the origin remote under
// `configRoot`/repos/) says dynaflows.enabled true. `machineDynaflowsPath` (~/.omp/dynaflows.json)
// is the machine default: {"enabled": false} turns dynaflows off for every repo on this machine;
// absent or true defers to the record. No origin or no record means off. `configPath` is the rails
// mapping file (agent/rails.yml shared with generic rails).
async function gitValue(pi: ExecApi, cwd: string, args: string[]): Promise<string> {
	const result = await pi.exec("git", args, { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.code !== 0 || result.killed || !result.stdout.trim()) throw new Error(`cannot resolve Git scope: ${result.stderr}`);
	return result.stdout.trim();
}
async function primaryGithubRepository(pi: ExecApi, cwd: string): Promise<string> {
	const remotes = (await gitValue(pi, cwd, ["remote"])).split("\n");
	const remote = remotes.includes("origin") ? "origin" : remotes.length === 1 ? remotes[0] : undefined;
	if (!remote) throw new Error("cannot resolve primary GitHub remote");
	const url = await gitValue(pi, cwd, ["remote", "get-url", remote]);
	const match = /^(?:https:\/\/github\.com\/|git@github\.com:|ssh:\/\/git@github\.com\/)([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?)(?:\.git)?$/.exec(url);
	if (!match?.[1]) throw new Error("primary remote is not a validated GitHub repository");
	return match[1];
}
function repoExec(pi: ExecApi, argv: string[], cwd: string, timeout?: number) {
	return pi.exec("env", [...harnessVariables.flatMap(name => ["-u", name]), "--", ...argv], timeout === undefined ? { cwd } : { cwd, timeout });
}
async function proveResultReason(pi: ExecApi, cwd: string, head: string): Promise<string | undefined> {
	const common = await pi.exec("git", ["rev-parse", "--git-common-dir"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (common.code !== 0 || common.killed) throw new Error("cannot resolve Git common directory for the prove() result");
	const path = resolve(cwd, common.stdout.trim(), "omp-prove", `${head}.result.json`);
	const result = z.object({ head: z.literal(head), status: z.literal("pass") });
	const passed = await readFile(path, "utf8").then(text => result.safeParse(JSON.parse(text)).success).catch(() => false);
	if (passed) return undefined;
	return `Agent rails: no prove() pass for ${head} at ${path}; run prove() for this head, then merge.`;
}
async function structureResultReason(pi: ExecApi, cwd: string, head: string): Promise<string | undefined> {
	const common = await pi.exec("git", ["rev-parse", "--git-common-dir"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (common.code !== 0 || common.killed || !common.stdout.trim()) throw new Error("cannot resolve Git common directory for the structure_review() result");
	const path = resolve(cwd, common.stdout.trim(), "omp-structure", `${head}.json`);
	const finding = z.object({
		unit: z.string(), question: z.string(), status: z.enum(["ready", "provisional"]),
		score: z.number().min(0).max(1), threshold: z.number().min(0).max(1),
	}).strict();
	const schema = z.object({
		head: z.literal(head), halt: z.boolean(), status: z.enum(["shadow", "incomplete", "blocked"]),
		findings: z.array(finding), parent: z.string().nullable().optional(),
		units: z.record(z.string(), z.unknown()).optional(), errors: z.array(z.string()).optional(),
		thresholds: z.unknown().optional(), version_key: z.string().nullable().optional(),
		model_reported: z.string().nullable().optional(), model_note: z.string().optional(),
		prComment: z.string().optional(),
	}).strict();
	let result;
	try { result = schema.parse(JSON.parse(await readFile(path, "utf8"))); }
	catch { return `Agent rails: no valid structure_review() result for ${head} at ${path}; run structure_review() for this head, then merge.`; }
	if (!result.halt) return;
	const ready = result.findings.filter(item => item.status === "ready").map(item => `${item.unit}: ${item.question}`);
	return `Agent rails: ready structure findings block ${head}: ${ready.join("; ") || "halt is true"}; fix them and rerun prove() and structure_review() on the new head.`;
}
type PrCi = { head?: string; repository?: string; reason?: string | undefined };
type PrIdentity = { head: string; repository: string };
async function foreignRepository(pi: ExecApi, root: string, selector: string | undefined, repository: string): Promise<boolean> {
	if (!selector || !/^https?:[/]{2}/i.test(selector)) return false;
	return (await primaryGithubRepository(pi, root)).toLowerCase() !== repository.toLowerCase();
}
function requiredCiReason(checks: CheckReport[], required: { requiredContext: string; runner: string }, pr: PrIdentity): string | undefined {
	const { requiredContext, runner } = required;
	const named = checks.filter(check => check.name === requiredContext);
	if (named.some(check => check.green)) return undefined;
	const command = (verb: string) => `python3 ${shellWord(ciRunnerPath)} ${verb} ${shellWord(runner)} ${pr.repository} ${pr.head}`;
	return `Agent rails: required CI context "${requiredContext}" is ${named[0]?.state ?? "missing"} for ${pr.head} in ${pr.repository} (runner ${runner}). ` +
		`Submit and wait: ${command("submit")}, then ${command("wait")} --context ${shellWord(requiredContext)}; ` +
		"see skill://git-tool/references/merge-and-recover.md for failures.";
}
async function noChecksReason(root: string, gate: Gate | undefined, head: string): Promise<string | undefined> {
	const workflows = await stat(resolve(root, ".github/workflows")).catch(absentFile);
	return gate?.ciCheck || workflows?.isDirectory() ? `Agent rails: no CI checks reported for ${head}` : undefined;
}
async function ciReason(root: string, checks: CheckReport[], gate: Gate | undefined, pr: PrIdentity): Promise<string | undefined> {
	if (gate?.requiredCi) return requiredCiReason(checks, gate.requiredCi, pr);
	if (!checks.length) return noChecksReason(root, gate, pr.head);
	const nonGreen = checks.filter(check => !check.acceptable).map(check => `${check.name}: ${check.state}`);
	return nonGreen.length || !checks.some(check => check.green)
		? `Agent rails: CI is not green for ${pr.head}: ${nonGreen.join(", ") || "no SUCCESS check reported"}` : undefined;
}
type DynaflowsAt = { on: boolean; off?: string; invalid?: boolean };
async function statusAdditions(pi: ExecApi, canonicalRoot: string): Promise<Set<string>> {
	const status = await pi.exec("git", ["status", "--porcelain=v1", "-z", "--untracked-files=all"], { cwd: canonicalRoot, timeout: SUBPROCESS_TIMEOUT_MS });
	if (status.code !== 0 || status.killed) {
		throw new Error(`git status failed${status.killed ? " (killed)" : ` (exit ${status.code})`}: ${status.stderr || status.stdout}`.trim());
	}
	return porcelainAdditions(status.stdout);
}
async function touchedOwnerPaths(pi: ExecApi, root: string, owned: string[]): Promise<string[]> {
	const touched = new Set<string>();
	for (const args of [["diff", "--cached", "--name-only", "--no-renames", "-z"], ["diff", "--name-only", "--no-renames", "-z"]]) {
		const result = await pi.exec("git", args, { cwd: root, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code !== 0 || result.killed) throw new Error(`git ${args.join(" ")} failed: ${result.stderr}`.trim());
		for (const path of result.stdout.split("\0")) if (path && ownerOnly(path, owned)) touched.add(path);
	}
	return [...touched].toSorted();
}
function proposalOnly(map: Mapping, event: ToolEvent, ctx: ExtensionContext, state: State): boolean {
	if (event.toolName === "ast_edit" && event.details.applied !== true) {
		state.proposal = event.details.files?.flatMap(path => absolute(path, ctx.cwd) ?? []) ?? [];
		return true;
	}
	if (event.toolName === "write" && event.input.path === map.rejectDevice) {
		state.proposal = [];
		return true;
	}
	return false;
}
function editedPaths(map: Mapping, event: ToolEvent, ctx: ExtensionContext, state: State): string[] {
	if (event.toolName !== "write" || event.input.path !== map.resolveDevice) return paths(event, map, ctx.cwd);
	state.proposal = [];
	const { applied, files } = event.details;
	return applied === true && files ? files.flatMap(path => absolute(path, ctx.cwd) ?? []) : [];
}
async function groupByRoot(map: Mapping, changed: string[], state: State): Promise<Map<string, string[]>> {
	const groups = new Map<string, string[]>();
	for (const path of changed) {
		const root = await repoRoot(path);
		if (!root) continue;
		if (matches(relative(root, path), map.codeGlobs)) state.edits.add(root);
		groups.set(root, [...(groups.get(root) ?? []), path]);
	}
	return groups;
}
type Notify = (notice: string, level?: "info" | "error") => string;
type EnabledRequest = { notice: string; slug: string; dir: string; task: boolean };

export default function dynaflows(
	pi: DynaflowsApi,
	{
		machineDynaflowsPath = fileURLToPath(new URL("../../../dynaflows.json", import.meta.url)),
		configPath = fileURLToPath(new URL("../../rails.yml", import.meta.url)),
		configRoot = defaultConfigRoot(),
		guardrailsCommand = defaultGuardrailsCommand,
	}: { machineDynaflowsPath?: string; configPath?: string; configRoot?: string; guardrailsCommand?: string[] } = {},
): void {
	const states = new Map<string, State>();
	let map: Mapping;
	let loadError: string | undefined;
	try {
		const parsed = mappingSchema.safeParse(Bun.YAML.parse(readFileSync(configPath, "utf8")));
		if (!parsed.success) throw new Error(`invalid mappings: ${parsed.error.message}`);
		map = parsed.data;
	} catch (error) {
		loadError = `Agent rails: cannot load rails.yml: ${String(error)}`;
	}
	const stateFor = (ctx: ExtensionContext) => {
		const id = ctx.sessionManager.getSessionId();
		let state = states.get(id);
		if (!state) {
			state = fresh();
			states.set(id, state);
		}
		return state;
	};
	async function recordAt(root: string): Promise<RepoRecord | undefined> {
		return readRecord(root, configRoot);
	}
	async function gateAt(root: string, state: State, ctx: ExtensionContext, strict = false): Promise<Gate | undefined> {
		try {
			return (await recordAt(root))?.gate;
		} catch (error) {
			if (strict) throw error;
			note(state, ctx, `Agent rails: ${error instanceof Error ? error.message : String(error)}; reference checks apply.`);
			return undefined;
		}
	}
	async function prCi(cwd: string | undefined, selector: string | undefined, state: State, ctx: ExtensionContext): Promise<PrCi> {
		if (!cwd) throw new Error("cannot resolve PR action cwd");
		const root = await repoRoot(cwd);
		if (!root) throw new Error("PR checks require a validated local Git repository");
		const result = await pi.exec("gh", ["pr", "view", ...(selector ? [selector] : []), "--json", "headRefOid,statusCheckRollup,url"],
			{ cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code !== 0 || result.killed) return {
			reason: `Agent rails: cannot read PR head/CI via gh: ${result.stderr.trim()}; authenticate the GitHub account that owns this repository (gh auth switch) and retry.`,
		};
		const pr = prCiSchema.parse(JSON.parse(result.stdout));
		const identity = { head: pr.headRefOid, repository: new URL(pr.url).pathname.split("/").slice(1, 3).join("/") };
		if (await foreignRepository(pi, root, selector, identity.repository)) {
			return { ...identity, reason: `Agent rails: run from a checkout of ${identity.repository}` };
		}
		const gate = await gateAt(root, state, ctx, true);
		return { ...identity, reason: await ciReason(root, pr.statusCheckRollup, gate, identity) };
	}
	async function machineDynaflows(state: State, ctx: ExtensionContext): Promise<Dynaflows | undefined> {
		try {
			const parsed = dynaflowsSchema.safeParse(JSON.parse(await readFile(machineDynaflowsPath, "utf8")));
			if (parsed.success) return parsed.data;
			note(state, ctx, `Agent rails: ${machineDynaflowsPath} is invalid; ignored.`);
		} catch (error) {
			const parsedError = fileErrorSchema.safeParse(error);
			if (!parsedError.success || parsedError.data.code !== "ENOENT") note(state, ctx, `Agent rails: ${machineDynaflowsPath} is unreadable; ignored.`);
		}
		return undefined;
	}
	// Whether dynaflows is on at `cwd`; when off, `off` says why in offText's words (the slug or
	// "none", then the reason). Strict callers throw on an invalid or refused record.
	async function recordSwitch(root: string, slug: string | null, dir: string, strict: boolean): Promise<DynaflowsAt> {
		try {
			const record = await recordAt(root);
			if (!record) return { on: false, off: offText(slug, `no per-repo record at ${join(dir, GATE_FILE)}`) };
			if (record.gate.dynaflows.enabled) return { on: true };
			return { on: false, off: offText(slug, `the record ${join(record.dir, GATE_FILE)} sets dynaflows.enabled false`) };
		} catch (error) {
			if (strict) throw error;
			return { on: false, invalid: true, off: offText(slug, error instanceof Error ? error.message : String(error)) };
		}
	}
	async function dynaflowsAt(ctx: ExtensionContext, cwd = ctx.cwd, strict = false): Promise<DynaflowsAt> {
		const state = stateFor(ctx);
		const root = await repoRoot(cwd);
		const resolved = root ? await resolveRecord(root, configRoot) : { slug: null, dir: null, reason: "not in a Git repository" };
		if ((await machineDynaflows(state, ctx))?.enabled === false) {
			return { on: false, off: offText(resolved.slug, `the machine default ${machineDynaflowsPath} sets enabled false`) };
		}
		if (!root || !resolved.dir) return { on: false, off: offText(resolved.slug, resolved.reason ?? "no per-repo record") };
		return recordSwitch(root, resolved.slug, resolved.dir, strict);
	}
	// The record's dynaflows switch. Non-strict callers note an invalid or refused record once.
	async function dynaflowsOn(ctx: ExtensionContext, cwd = ctx.cwd, strict = false): Promise<boolean> {
		const at = await dynaflowsAt(ctx, cwd, strict);
		if (at.invalid && at.off) note(stateFor(ctx), ctx, sentence(`Agent rails: ${at.off}`));
		return at.on;
	}
	// The dynaflows directive's target: the repo's slug and record directory (null when it has no
	// record yet). Throws outside a Git project with a GitHub origin remote.
	async function directiveTarget(ctx: ExtensionContext): Promise<{ slug: string; dir: string | null }> {
		const root = await repoRoot(ctx.cwd);
		if (!root) throw new Error("repo toggle needs a Git project; edit the machine default separately");
		const resolved = await resolveRecord(root, configRoot);
		if (!resolved.slug) throw new Error(`repo toggle needs a Git project with a GitHub origin remote: ${root} has ${resolved.reason}`);
		return { slug: resolved.slug, dir: resolved.dir };
	}
	async function runPathCheck(root: string, canonicalRoot: string, changed: string[]): Promise<void> {
		const record = await recordAt(root);
		if (!record) throw new Error("per-repo record disappeared during path check");
		const result = await repoExec(pi, [...harnessPathCommand, "--repo", canonicalRoot, "--record", record.dir, "--event", "edit", ...changed], canonicalRoot, SUBPROCESS_TIMEOUT_MS);
		if (result.code !== 0 || result.killed) throw new Error(`${result.stdout}\n${result.stderr}`.trim());
	}
	async function checkPaths(root: string, changed: string[], state: State): Promise<string | undefined> {
		let pending = changed;
		try {
			const canonicalRoot = await realpath(root);
			pending = await canonicalCandidates(root, canonicalRoot, pending);
			const added = await statusAdditions(pi, canonicalRoot);
			pending = pending.filter(path => added.has(relative(canonicalRoot, path)));
			if (pending.length) await runPathCheck(root, canonicalRoot, pending);
			state.pathFailures.delete(root);
			return undefined;
		} catch (error) {
			state.pathFailures.set(root, pending);
			return `Agent rails: path check failed in ${root}: ${String(error)}`;
		}
	}
	// The record's ownerOnlyPaths are the owner's alone (for example the dependency-audit exception
	// file). At every agent commit in a dynaflows-on repo with a record, rails
	// refuse the commit when a staged or unstaged tracked change touches one. Renames count as a
	// deletion and an addition, so moving an owner-only file away is refused too.
	async function ownerOnlyReason(root: string): Promise<string | undefined> {
		const record = await recordAt(root);
		if (!record || !record.gate.ownerOnlyPaths.length) return undefined;
		const touched = await touchedOwnerPaths(pi, root, record.gate.ownerOnlyPaths);
		if (!touched.length) return undefined;
		return `Agent rails: this commit changes owner-only ${touched.length === 1 ? "path" : "paths"} ${touched.join(", ")} ` +
			`(the record's ownerOnlyPaths in ${join(record.dir, GATE_FILE)}). Agents never change these paths, such as the ` +
			"dependency-audit exception file. Restore them (git restore --staged --worktree -- <path>), then stop and raise " +
			"the change to the owner: what you need changed, why, and the evidence. The owner makes the change.";
	}
	async function commitChecksReason(root: string): Promise<string | undefined> {
		const record = await recordAt(root);
		if (!record) return undefined;
		const pathResult = await repoExec(pi, [...harnessPathCommand, "--repo", root, "--record", record.dir, "--event", "staged"], root, SUBPROCESS_TIMEOUT_MS);
		if (pathResult.code !== 0 || pathResult.killed) {
			return `Agent rails: staged path check failed in ${root}: ${pathResult.stdout}\n${pathResult.stderr}\nFix the findings, then commit again.`;
		}
		const runs: [string, string[]][] = [
			[`checks.commit (${record.gate.commit.join(" ")})`, record.gate.commit],
			["harness checks (guardrails)", [...guardrailsCommand, "--repo", root, "--record", record.dir]],
		];
		for (const [label, argv] of runs) {
			const result = await repoExec(pi, argv, root);
			if (result.code === 0 && !result.killed) continue;
			const output = `${result.stdout}\n${result.stderr}`.trim();
			const owned = argv === record.gate.commit ? "" : `\n${harnessOnlyNote}`;
			return `Agent rails: ${label} failed in ${root}${result.killed ? " (killed)" : ` (exit ${result.code})`}; ` +
				`fix every finding in the repo, then commit again:\n${output.length > commitOutputLimit ? `…${output.slice(-commitOutputLimit)}` : output}${owned}`;
		}
		return undefined;
	}
	async function checkGroups(groups: Map<string, string[]>, ctx: ExtensionContext, state: State): Promise<string[]> {
		const errors: string[] = [];
		for (const [root, changedPaths] of groups) {
			if (!await dynaflowsOn(ctx, root)) continue;
			const pending = [...new Set([...(state.pathFailures.get(root) ?? []), ...changedPaths])];
			const error = await checkPaths(root, pending, state);
			if (error) errors.push(error);
		}
		return errors;
	}
	async function recordResult(event: ToolEvent, ctx: ExtensionContext, state: State, runCheck: boolean) {
		recordRead(event, state);
		if (event.isError || proposalOnly(map, event, ctx, state) || !map.editTools.includes(event.toolName)) return undefined;
		const groups = await groupByRoot(map, editedPaths(map, event, ctx, state), state);
		const errors = runCheck ? await checkGroups(groups, ctx, state) : [];
		return errors.length ? { additionalContext: errors.join("\n") } : undefined;
	}
	async function replayEntry(entry: ReturnType<ExtensionContext["sessionManager"]["getBranch"]>[number], calls: Map<string, ToolEvent>, ctx: ExtensionContext, state: State): Promise<void> {
		if (entry.type === "compaction") { state.reads.clear(); return; }
		if (entry.type !== "message") return;
		const message = entry.message;
		if (message.role === "assistant") for (const block of message.content) {
			if (block.type !== "toolCall") continue;
			calls.set(block.id, policyEvent({ type: "tool_call", toolName: block.name, toolCallId: block.id, input: block.arguments }));
		}
		if (message.role !== "toolResult") return;
		const call = calls.get(message.toolCallId);
		if (call) await recordResult({ ...call, isError: message.isError, details: detailsSchema.parse(message.details) }, ctx, state, false);
	}
	async function rebuild(_event: SessionStartEvent | SessionSwitchEvent | SessionBranchEvent | SessionTreeEvent, ctx: ExtensionContext): Promise<void> {
		const state = fresh();
		const previous = states.get(ctx.sessionManager.getSessionId());
		state.pointed = previous?.pointed ?? false;
		state.pathFailures = previous?.pathFailures ?? new Map();
		states.set(ctx.sessionManager.getSessionId(), state);
		if (loadError) { note(state, ctx, loadError); return; }
		const calls = new Map<string, ToolEvent>();
		for (const entry of ctx.sessionManager.getBranch()) await replayEntry(entry, calls, ctx, state);
	}
	pi.on("session_start", async (event, ctx) => {
		await rebuild(event, ctx);
		if (loadError) return;
		const at = await dynaflowsAt(ctx).catch(error => ({ on: false, off: offText(null, String(error)) }));
		if (!at.on) {
			const rules = lookup("ttsr.disabledRules");
			if (!rules) throw new Error("Agent rails: ttsr.disabledRules setting is unavailable");
			rules.override(pi.pi.settings, [...new Set([...stringList.parse(rules.get(pi.pi.settings)), ...map.dynaflowsRules])]);
		}
		if (!at.on && at.off) note(stateFor(ctx), ctx, sentence(`Agent rails: ${at.off}`));
	});
	pi.on("session_switch", rebuild);
	pi.on("session_branch", rebuild);
	pi.on("session_tree", rebuild);
	pi.on("session_compact", (_event, ctx) => {
		const state = stateFor(ctx);
		state.reads.clear();
	});
	async function commitActionReason(action: GitAction, ctx: ExtensionContext): Promise<string | undefined> {
		if (!action.cwd) return "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.";
		const root = await repoRoot(action.cwd);
		const at = root ? await dynaflowsAt(ctx, root, true) : await dynaflowsAt(ctx, action.cwd);
		if (!at.on && at.off) ctx.ui.notify(`Agent rails: ${at.off}; commit checks do not run.`, "info");
		if (!root || !at.on) return undefined;
		return (await ownerOnlyReason(root)) ?? commitChecksReason(root);
	}
	async function mergeActionReason(action: GitAction, state: State, ctx: ExtensionContext): Promise<string | undefined> {
		if (!action.cwd) throw new Error("cannot resolve PR action cwd");
		if (!await dynaflowsOn(ctx, action.cwd)) return undefined;
		const head = matchedHead(action.args);
		const format = headFormatReason(head);
		if (format || !head) return format;
		const evidence = (await proveResultReason(pi, action.cwd, head)) ?? (await structureResultReason(pi, action.cwd, head));
		if (evidence) return evidence;
		const pr = await prCi(action.cwd, prSelector(action.args), state, ctx);
		if (pr.head && pr.head !== head) return `Agent rails: PR head is ${pr.head}, not ${head}; prove() the current head`;
		return pr.reason;
	}
	async function gitActionReason(action: GitAction, state: State, ctx: ExtensionContext): Promise<string | undefined> {
		if (!new RegExp(map.gitCommand).test(action.command)) return undefined;
		const commit = /^git commit\b/.test(action.command) ? await commitActionReason(action, ctx) : undefined;
		if (commit) return commit;
		return /^gh pr merge\b/.test(action.command) ? mergeActionReason(action, state, ctx) : undefined;
	}
	async function bashReason(event: ToolEvent, ctx: ExtensionContext, state: State): Promise<string | undefined> {
		if (event.toolName !== "bash" || event.input.command === undefined) return undefined;
		const cwd = absolute(event.input.cwd ?? ctx.cwd, ctx.cwd) ?? ctx.cwd;
		for (const action of await gitActions(event.input.command, cwd)) {
			const reason = await gitActionReason(action, state, ctx);
			if (reason) return reason;
		}
		return undefined;
	}
	async function pathTriggers(path: string, ctx: ExtensionContext, state: State): Promise<string[]> {
		const root = await repoRoot(path);
		if (!root || !await dynaflowsOn(ctx, root)) return [];
		const rel = relative(root, path);
		const code = matches(rel, map.codeGlobs);
		const triggers: string[] = [];
		if (!state.edits.has(root) && code) triggers.push("firstEdit");
		if (code && !(await access(path).then(() => true, () => false))) triggers.push("newFile");
		if (matches(rel, map.testGlobs)) triggers.push("testFile");
		return triggers;
	}
	async function editReadsReason(event: ToolEvent, ctx: ExtensionContext, state: State): Promise<string | undefined> {
		if (!map.editTools.includes(event.toolName)) return undefined;
		const targets = event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
		const required = new Set<string>();
		for (const path of targets) {
			for (const url of missing(state, map, await pathTriggers(path, ctx, state))) required.add(url);
		}
		return required.size ? readReason([...required]) : undefined;
	}
	pi.on("tool_call", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { block: true, reason: loadError };
		const state = stateFor(ctx);
		try {
			const reason = (await bashReason(event, ctx, state)) ?? (await editReadsReason(event, ctx, state));
			return reason ? { block: true, reason } : undefined;
		} catch (error) { return { block: true, reason: `Agent rails: policy could not be evaluated: ${String(error)}` }; }
	});
	pi.on("tool_result", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { additionalContext: loadError };
		try { return await recordResult(event, ctx, stateFor(ctx), true); }
		catch (error) { return { additionalContext: `Agent rails: post-edit policy failed: ${String(error)}` }; }
	});
	async function handoffReasons(ctx: ExtensionContext, state: State): Promise<string[]> {
		const reasons: string[] = [];
		let editedDynaflowsRepo = false;
		for (const root of new Set([...state.edits, ...state.pathFailures.keys()])) {
			if (!await dynaflowsOn(ctx, root)) continue;
			if (state.edits.has(root)) editedDynaflowsRepo = true;
			const failedPaths = state.pathFailures.get(root);
			const error = failedPaths ? await checkPaths(root, failedPaths, state) : undefined;
			if (error) reasons.push(error);
		}
		const urls = editedDynaflowsRepo ? missing(state, map, ["verification"]) : [];
		if (urls.length) reasons.push(`read ${urls.join(" and ")}`);
		return reasons;
	}
	pi.on("session_stop", async (_event, ctx) => {
		if (loadError) return { decision: "block", reason: loadError };
		const state = stateFor(ctx);
		try {
			const reasons = await handoffReasons(ctx, state);
			return reasons.length ? { decision: "block", reason: `Agent rails: ${reasons.join("; ")}, then retry handoff.` } : undefined;
		} catch (error) { return { decision: "block", reason: `Agent rails: handoff policy failed: ${String(error)}` }; }
	});
	// Applies the owner's dynaflows ask for the current repo: sets the record's switch, grants (or, on
	// off, revokes) this session's record writes, and returns the next step for the agent, or none.
	// `task` is true when the prompt also holds work to route.
	const workflowReferences = () => (map.references["workflow"] ?? []).join(" and ");
	async function enabledOutcome(ctx: ExtensionContext, state: State, notify: Notify, request: EnabledRequest): Promise<Applied> {
		const at = await dynaflowsAt(ctx);
		if (at.invalid) return { notice: request.notice, content: resumeContent(request.slug, request.dir, at.off ?? "invalid record") };
		if (!at.on) {
			const off = sentence(at.off ?? "dynaflows off");
			return { notice: notify(`Agent rails: ${off}`), content: `${off.charAt(0).toUpperCase()}${off.slice(1)} Do not apply dynaflows.` };
		}
		state.pointed = true;
		if (request.task) return { notice: request.notice, content: `Dynaflows on. Route this work with ${workflowReferences()}.` };
		return { notice: request.notice, content: startContent(request.slug, workflowReferences()) };
	}
	async function applyDynaflows(ctx: ExtensionContext, enabled: boolean, task: boolean): Promise<Applied> {
		const state = stateFor(ctx);
		const notify: Notify = (notice, level = "info") => { ctx.ui.notify(notice, level); return notice; };
		let target: { slug: string; dir: string | null };
		try { target = await directiveTarget(ctx); }
		catch (error) { return { notice: notify(`Agent rails: ${String(error)}`, "error") }; }
		state.pointed = false;
		const { slug, dir } = target;
		if (!dir) return missingRecordOutcome(notify, slug, recordDir(slug, configRoot), enabled);
		let notice: string;
		try {
			notice = notify(`Agent rails: dynaflows ${enabled ? "on" : "off"} (${await setDynaflows(dir, enabled)}).`);
		} catch (error) {
			const reason = error instanceof Error ? error.message : String(error);
			notice = notify(`Agent rails: cannot set dynaflows in ${join(dir, GATE_FILE)}: ${reason}`, "error");
			return { notice, content: enabled ? resumeContent(slug, dir, reason) : undefined };
		}
		if (!enabled) return { notice, content: "Dynaflows off: do not apply dynaflows." };
		return enabledOutcome(ctx, state, notify, { notice, slug, dir, task });
	}
	pi.on("before_agent_start", async (event, ctx) => {
		if (loadError || ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const state = stateFor(ctx);
		const lines = (event.prompt ?? "").split("\n").filter(line => line.trim());
		const settings = lines.map(dynaflowsSetting);
		const asked = new Set(settings.filter(setting => setting !== undefined));
		if (asked.size > 1) {
			ctx.ui.notify("Agent rails: contradictory dynaflows directives; no setting changed.", "error");
			return agentMessage("The owner's message asks to both enable and disable dynaflows, so no setting changed. " +
				"Ask the owner which they want, then call the dynaflows tool with their answer.");
		}
		if (asked.size) {
			// A line with no directive is the task to route.
			const { content } = await applyDynaflows(ctx, asked.has(true), settings.some(setting => setting === undefined));
			return content === undefined ? undefined : agentMessage(content);
		}
		if (state.pointed || !(await dynaflowsOn(ctx))) return;
		state.pointed = true;
		return agentMessage(`Dynaflows on. Route this work with ${workflowReferences()}.`);
	});
	const dynaflowsToolParams = z.object({ enabled: z.boolean().describe("true when the user asked to enable dynaflows, false to disable it") });
	pi.registerTool({
		name: "dynaflows",
		label: "Dynaflows",
		description: "Turn dynaflows on or off for the current repository and get the next step. Call it only when the " +
			"user asked in this conversation to enable or disable dynaflows, in any wording; never on your own initiative.",
		parameters: dynaflowsToolParams,
		loadMode: "essential",
		approval: "write",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			if (loadError) return toolReply(loadError);
			if (ctx.agent.kind !== "main") return toolReply("Agent rails: only the main agent can toggle dynaflows.");
			const result = await applyDynaflows(ctx, dynaflowsToolParams.parse(params).enabled, false);
			return toolReply(result.content ?? result.notice);
		},
	});
	pi.registerCommand("extension-health-dynaflows", {
		description: "Dynaflows registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Dynaflows registered", loadError ? "error" : "info"); },
	});
}
