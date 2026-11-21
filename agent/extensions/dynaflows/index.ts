import { access, lstat, readFile, realpath, stat } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { isAbsolute, join, relative, resolve } from "node:path";
import {
	z, type ExtensionAPI, type ExtensionContext,
	type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent,
} from "@oh-my-pi/pi-coding-agent";
import { lookup } from "@oh-my-pi/pi-coding-agent/config/registry";
import {
	configRoot as defaultConfigRoot, GATE_FILE, type Gate, loadGate, minimalText, recordDir, resolveRecord,
} from "./lib/repo-record.ts";
import { defaultModesPath, MODES, type Mode, repoMode, writeMode } from "./lib/modes.ts";
import {
	SUBPROCESS_TIMEOUT_MS, stringList, mappingSchema, detailsSchema, policyEvent, absolute, repoRoot,
	paths, pathArguments, matches, missing, sentence, readReason, gitActions, readRecord, note, absentFile, fileErrorSchema,
	recordRead, subagentPathReason, type GitAction, type Mapping, type RepoRecord, type State as SharedState, type ToolEvent,
} from "./lib/rails-runtime.ts";
import {
	PUBLISHES, adminMergeReason, agentMergeReason, commitHookReason, hookBypassReason, mergeMethodReason, mergePolicyFor, minimalMergeReason,
	pushReason, routeReason, worktreeReason,
} from "./lib/guards.ts";

const prCiSchema = z.object({
	headRefOid: z.string().regex(/^[0-9a-f]{40}$/),
	baseRefName: z.string().min(1),
	headRefName: z.string().min(1),
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
const ALIAS_DEPTH = 8;
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
function missingRecordOutcome(notify: (notice: string) => string, slug: string, recordPath: string, mode: Mode): Applied {
	if (mode === "minimal") return { notice: notify(`Agent rails: ${slug} has no per-repo record, so dynaflows is already in minimal mode.`) };
	return { notice: notify(`Agent rails: ${slug} has no per-repo record; starting repository setup.`), content:
		`The owner asked for dynaflows structured mode in ${slug}, which has no per-repo record yet. ` +
		"Start skill://structured-workflow/references/harness-setup.md setup now, from Step 0. Write the record " +
		`yourself at ${recordPath} as setup reaches each file; structured mode starts once the record is complete. ` +
		"Never ask the owner to save a file." };
}
function resumeContent(slug: string, dir: string, reason: string): string {
	return `The owner asked for dynaflows structured mode in ${slug}, but its per-repo record ` +
		`${join(dir, GATE_FILE)} is refused: ${reason}. Resume skill://structured-workflow/references/harness-setup.md setup now and repair ` +
		"the record yourself in place. Never ask the owner to save a file.";
}
function startContent(slug: string, workflow: string): string {
	return `Dynaflows structured mode for ${slug}. The owner asked to start dynaflows here, so begin now: read ` +
		`${workflow} and assess this repo against its Start here playbook selection ` +
		"(Project setup when controls, the Verification surface or the feature map are missing). Report the stage you found " +
		"and start it. If the owner's message also names a task, route that task; if setup is complete and no task was " +
		"named, report that dynaflows is ready and ask the owner for the task.";
}
const agentMessage = (content: string) => ({ message: { customType: "dynaflows", content, display: false, attribution: "agent" as const } });
const toolReply = (text: string) => ({ content: [{ type: "text" as const, text }], details: {} });
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
	"code to the harness copy: route to project setup (skill://structured-workflow/references/harness-setup.md, adopt mode) to " +
	"draft guardrails.check.repo for the owner to save.";

const dynaflowsOnVerbs = "on|enable|init|initiali[sz]e|start|setup|set up|turn on|structured";
const dynaflowsDirective = new RegExp(
	`^(?:\\$dynaflows(?: (${dynaflowsOnVerbs}|off|disable|stop|minimal))?|(${dynaflowsOnVerbs}|disable|stop|turn off|minimal) \\$dynaflows)$`, "i");
function dynaflowsSetting(line: string): Mode | undefined {
	const match = dynaflowsDirective.exec(line.trim().replace(/\s+/g, " "));
	if (!match) return undefined;
	return /^(?:off|disable|stop|turn off|minimal)$/i.test(match[1] ?? match[2] ?? "") ? "minimal" : "structured";
}

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
type PrCi = { reason: string } | { head: string; repository: string; base: string; headRef: string; gate: Gate | undefined; ciReason: string | undefined };
type PrIdentity = { head: string; repository: string };
type PrState = Exclude<PrCi, { reason: string }>;
// The merge policy for the PR's base and head decides the method and whether proof is needed.
async function policyMergeReason(pi: ExecApi, args: string[], at: { cwd: string; head: string }, pr: PrState): Promise<string | undefined> {
	const { cwd, head } = at;
	const policy = mergePolicyFor(pr.gate?.mergePolicies ?? [], pr.base, pr.headRef);
	const method = mergeMethodReason(args, policy);
	if (method) return method;
	if (pr.head !== head) return `Agent rails: PR head is ${pr.head}, not ${head}; ${policy.proof ? "prove() the current head" : "merge the current head"}`;
	const evidence = policy.proof ? (await proveResultReason(pi, cwd, head)) ?? (await structureResultReason(pi, cwd, head)) : undefined;
	return evidence ?? pr.ciReason;
}
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
type ModeAt = { mode: Mode; why?: string; invalid?: boolean };
async function statusAdditions(pi: ExecApi, canonicalRoot: string): Promise<Set<string>> {
	const status = await pi.exec("git", ["status", "--porcelain=v1", "-z", "--untracked-files=all"], { cwd: canonicalRoot, timeout: SUBPROCESS_TIMEOUT_MS });
	if (status.code !== 0 || status.killed) {
		throw new Error(`git status failed${status.killed ? " (killed)" : ` (exit ${status.code})`}: ${status.stderr || status.stdout}`.trim());
	}
	return porcelainAdditions(status.stdout);
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
type Resolved = { slug: string | null; dir: string | null; reason?: string };
// The mode file names the mode; structured mode also needs a valid record, else the repo runs minimal.
async function resolvedMode(modesPath: string, resolved: Resolved, strict: boolean): Promise<ModeAt> {
	const { slug, dir } = resolved;
	if (!slug) return { mode: "minimal", why: minimalText(null, resolved.reason ?? "no GitHub origin") };
	try {
		if (await repoMode(modesPath, slug) === "minimal") return { mode: "minimal", why: minimalText(slug, `the mode file ${modesPath} does not set structured`) };
		if (!dir) return { mode: "minimal", why: minimalText(slug, resolved.reason ?? "no per-repo record") };
		await loadGate(dir);
		return { mode: "structured" };
	} catch (error) {
		if (strict) throw error;
		return { mode: "minimal", invalid: true, why: minimalText(slug, error instanceof Error ? error.message : String(error)) };
	}
}
type SessionCache = { builtins?: Promise<Set<string>>; userRules?: string[] };
async function gitBuiltins(pi: ExecApi, cache: SessionCache, cwd: string): Promise<Set<string>> {
	cache.builtins ??= gitValue(pi, cwd, ["--list-cmds=builtins"]).then(text => new Set(text.split("\n")));
	return cache.builtins;
}
async function aliasActions(pi: ExecApi, cache: SessionCache, action: GitAction): Promise<GitAction[] | undefined> {
	const [name, ...rest] = action.args;
	if (!action.cwd || !action.command.startsWith("git ") || !name || name.startsWith("-")) return undefined;
	if ((await gitBuiltins(pi, cache, action.cwd)).has(name)) return undefined;
	const result = await pi.exec("git", ["config", "--get", `alias.${name}`], { cwd: action.cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.killed) throw new Error(`git config --get alias.${name} was killed`);
	const expansion = result.code === 0 ? result.stdout.trim() : "";
	if (!expansion) return undefined;
	const words = rest.map(shellWord).join(" ");
	if (expansion.startsWith("!")) return gitActions(`${expansion.slice(1)} ${words}`, action.cwd);
	const config = action.config.map(entry => `-c ${shellWord(entry)}`).join(" ");
	return gitActions(`git ${config} ${expansion} ${words}`, action.cwd);
}
async function subagentReason(map: Mapping, event: ToolEvent, ctx: ExtensionContext, state: State): Promise<string | undefined> {
	if (!map.editTools.includes(event.toolName) || ctx.agent.kind === "main") return undefined;
	return subagentPathReason([...pathArguments(event, map), ...(event.input.path === map.resolveDevice ? state.proposal : [])]);
}
type Notify = (notice: string, level?: "info" | "error") => string;
type StructuredRequest = { notice: string; slug: string; dir: string; task: boolean };

export default function dynaflows(
	pi: DynaflowsApi,
	{
		modesPath = defaultModesPath(),
		configPath = fileURLToPath(new URL("./rails.yml", import.meta.url)),
		configRoot = defaultConfigRoot(),
		guardrailsCommand = defaultGuardrailsCommand,
		ompCommand = ["omp"],
	}: { modesPath?: string; configPath?: string; configRoot?: string; guardrailsCommand?: string[]; ompCommand?: string[] } = {},
): void {
	const states = new Map<string, State>();
	const session: SessionCache = {};
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
		const result = await pi.exec("gh", ["pr", "view", ...(selector ? [selector] : []), "--json", "headRefOid,baseRefName,headRefName,statusCheckRollup,url"],
			{ cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code !== 0 || result.killed) return {
			reason: `Agent rails: cannot read PR head/CI via gh: ${result.stderr.trim()}; authenticate the GitHub account that owns this repository (gh auth switch) and retry.`,
		};
		const pr = prCiSchema.parse(JSON.parse(result.stdout));
		const identity = { head: pr.headRefOid, repository: new URL(pr.url).pathname.split("/").slice(1, 3).join("/") };
		if (await foreignRepository(pi, root, selector, identity.repository)) {
			return { reason: `Agent rails: run from a checkout of ${identity.repository}` };
		}
		const gate = await gateAt(root, state, ctx, true);
		return { ...identity, base: pr.baseRefName, headRef: pr.headRefName, gate, ciReason: await ciReason(root, pr.statusCheckRollup, gate, identity) };
	}
	async function modeAt(ctx: ExtensionContext, cwd = ctx.cwd, strict = false): Promise<ModeAt> {
		const root = await repoRoot(cwd);
		const resolved: Resolved = root ? await resolveRecord(root, configRoot) : { slug: null, dir: null, reason: "not in a Git repository" };
		return resolvedMode(modesPath, resolved, strict);
	}
	async function modeOf(ctx: ExtensionContext, cwd = ctx.cwd): Promise<Mode> {
		const at = await modeAt(ctx, cwd);
		if (at.invalid && at.why) note(stateFor(ctx), ctx, sentence(`Agent rails: ${at.why}`));
		return at.mode;
	}
	async function structuredAt(ctx: ExtensionContext, cwd = ctx.cwd): Promise<boolean> {
		return await modeOf(ctx, cwd) === "structured";
	}
	// The dynaflows directive's target: the repo's slug and record directory (null when it has no
	// record yet). Throws outside a Git project with a GitHub origin remote.
	async function directiveTarget(ctx: ExtensionContext): Promise<{ slug: string; dir: string | null }> {
		const root = await repoRoot(ctx.cwd);
		if (!root) throw new Error("repo toggle needs a Git project");
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
			if (!await structuredAt(ctx, root)) continue;
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
	async function syncRules(ctx: ExtensionContext): Promise<ModeAt> {
		const at = await modeAt(ctx).catch((error): ModeAt => ({ mode: "minimal", why: minimalText(null, String(error)) }));
		const rules = lookup("ttsr.disabledRules");
		if (!rules) throw new Error("Agent rails: ttsr.disabledRules setting is unavailable");
		const user = session.userRules ??= stringList.parse(rules.get(pi.pi.settings));
		rules.override(pi.pi.settings, at.mode === "structured" ? user : [...new Set([...user, ...map.structuredRules])]);
		return at;
	}
	pi.on("session_start", async (event, ctx) => {
		await rebuild(event, ctx);
		if (loadError) return;
		const at = await syncRules(ctx);
		if (at.mode === "minimal" && at.why) note(stateFor(ctx), ctx, sentence(`Agent rails: ${at.why}`));
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
		const at = root ? await modeAt(ctx, root, true) : await modeAt(ctx, action.cwd);
		if (at.why) ctx.ui.notify(`Agent rails: ${at.why}; commit checks do not run.`, "info");
		if (!root || at.mode !== "structured") return undefined;
		return commitChecksReason(root);
	}
	async function mergeActionReason(action: GitAction, state: State, ctx: ExtensionContext): Promise<string | undefined> {
		if (!action.cwd) throw new Error("cannot resolve PR action cwd");
		const head = matchedHead(action.args);
		const format = headFormatReason(head) ?? adminMergeReason(action.args);
		if (format || !head) return format;
		const pr = await prCi(action.cwd, prSelector(action.args), state, ctx);
		return "head" in pr ? policyMergeReason(pi, action.args, { cwd: action.cwd, head }, pr) : pr.reason;
	}
	async function publishActionReason(action: GitAction, mode: Mode, state: State, ctx: ExtensionContext): Promise<string | undefined> {
		const merge = /^gh pr merge\b/.test(action.command);
		const reason = (await agentMergeReason(action, configRoot)) ?? (merge ? minimalMergeReason(mode) : undefined);
		if (reason) return reason;
		if (/^git push\b/.test(action.command)) return pushReason(pi, action);
		return merge ? mergeActionReason(action, state, ctx) : undefined;
	}
	async function gitActionReason(action: GitAction, state: State, ctx: ExtensionContext): Promise<string | undefined> {
		const bypass = hookBypassReason(action) ?? routeReason(action);
		if (bypass || !new RegExp(map.gitCommand).test(action.command)) return bypass;
		const mode = action.cwd ? await modeOf(ctx, action.cwd) : "minimal";
		const urls = missing(state, map, mode, ["git"]);
		if (urls.length) return readReason(urls, mode);
		if (/^git worktree add\b/.test(action.command)) return worktreeReason(pi, ompCommand, action);
		if (/^git commit\b/.test(action.command)) return commitHookReason(action) ?? await commitActionReason(action, ctx);
		return PUBLISHES.test(action.command) ? publishActionReason(action, mode, state, ctx) : undefined;
	}
	async function actionsReason(actions: GitAction[], state: State, ctx: ExtensionContext, depth: number): Promise<string | undefined> {
		if (depth > ALIAS_DEPTH) return `Agent rails: Git aliases nest deeper than ${ALIAS_DEPTH} levels; run the Git command itself.`;
		for (const action of actions) {
			const expanded = await aliasActions(pi, session, action);
			const reason = expanded ? await actionsReason(expanded, state, ctx, depth + 1) : await gitActionReason(action, state, ctx);
			if (reason) return reason;
		}
		return undefined;
	}
	async function bashReason(event: ToolEvent, ctx: ExtensionContext, state: State): Promise<string | undefined> {
		if (event.toolName !== "bash" || event.input.command === undefined) return undefined;
		const cwd = absolute(event.input.cwd ?? ctx.cwd, ctx.cwd) ?? ctx.cwd;
		return actionsReason(await gitActions(event.input.command, cwd), state, ctx, 0);
	}
	async function pathTriggers(path: string, ctx: ExtensionContext, state: State): Promise<string[]> {
		const root = await repoRoot(path);
		if (!root || !await structuredAt(ctx, root)) return [];
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
			for (const url of missing(state, map, "structured", await pathTriggers(path, ctx, state))) required.add(url);
		}
		return required.size ? readReason([...required], "structured") : undefined;
	}
	pi.on("tool_call", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { block: true, reason: loadError };
		const state = stateFor(ctx);
		try {
			const reason = (await bashReason(event, ctx, state)) ?? (await subagentReason(map, event, ctx, state)) ??
				(await editReadsReason(event, ctx, state));
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
			if (!await structuredAt(ctx, root)) continue;
			if (state.edits.has(root)) editedDynaflowsRepo = true;
			const failedPaths = state.pathFailures.get(root);
			const error = failedPaths ? await checkPaths(root, failedPaths, state) : undefined;
			if (error) reasons.push(error);
		}
		const urls = editedDynaflowsRepo ? missing(state, map, "structured", ["verification"]) : [];
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
	const workflowReferences = () => (map.references.structured["workflow"] ?? []).join(" and ");
	async function structuredOutcome(ctx: ExtensionContext, state: State, notify: Notify, request: StructuredRequest): Promise<Applied> {
		const at = await modeAt(ctx);
		if (at.invalid) return { notice: request.notice, content: resumeContent(request.slug, request.dir, at.why ?? "invalid record") };
		if (at.mode === "minimal") {
			const minimal = sentence(at.why ?? "dynaflows minimal mode");
			return { notice: notify(`Agent rails: ${minimal}`), content: `${minimal.charAt(0).toUpperCase()}${minimal.slice(1)} Do not apply the dynaflows workflow.` };
		}
		state.pointed = true;
		if (request.task) return { notice: request.notice, content: `Dynaflows structured mode. Route this work with ${workflowReferences()}.` };
		return { notice: request.notice, content: startContent(request.slug, workflowReferences()) };
	}
	async function applyDynaflows(ctx: ExtensionContext, mode: Mode, task: boolean): Promise<Applied> {
		const state = stateFor(ctx);
		const notify: Notify = (notice, level = "info") => { ctx.ui.notify(notice, level); return notice; };
		let target: { slug: string; dir: string | null };
		try { target = await directiveTarget(ctx); }
		catch (error) { return { notice: notify(`Agent rails: ${String(error)}`, "error") }; }
		state.pointed = false;
		const { slug, dir } = target;
		try { await writeMode(modesPath, slug, mode); }
		catch (error) { return { notice: notify(`Agent rails: cannot set dynaflows for ${slug}: ${String(error)}`, "error") }; }
		await syncRules(ctx);
		if (!dir) return missingRecordOutcome(notify, slug, recordDir(slug, configRoot), mode);
		const notice = notify(`Agent rails: dynaflows ${mode} mode for ${slug} (${modesPath}).`);
		if (mode === "minimal") return { notice, content: "Dynaflows minimal mode: fixed guards only; do not apply the dynaflows workflow." };
		return structuredOutcome(ctx, state, notify, { notice, slug, dir, task });
	}
	async function applyDirective(ctx: ExtensionContext, settings: (Mode | undefined)[]) {
		const mode = settings.includes("structured") ? "structured" : "minimal";
		const { content } = await applyDynaflows(ctx, mode, settings.includes(undefined));
		return content === undefined ? undefined : agentMessage(content);
	}
	pi.on("before_agent_start", async (event, ctx) => {
		if (loadError || ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const state = stateFor(ctx);
		const lines = (event.prompt ?? "").split("\n").filter(line => line.trim());
		const settings = lines.map(dynaflowsSetting);
		const asked = new Set(settings.filter(setting => setting !== undefined));
		if (asked.size > 1) {
			ctx.ui.notify("Agent rails: contradictory dynaflows directives; no setting changed.", "error");
			return agentMessage("The owner's message asks for both dynaflows minimal and structured mode, so no setting changed. " +
				"Ask the owner which they want, then call the dynaflows tool with their answer.");
		}
		if (asked.size) return applyDirective(ctx, settings);
		if (state.pointed || !(await structuredAt(ctx))) return;
		state.pointed = true;
		return agentMessage(`Dynaflows structured mode. Route this work with ${workflowReferences()}.`);
	});
	const dynaflowsToolParams = z.object({
		mode: z.enum(MODES).describe("structured when the user asked to enable or start dynaflows, minimal when they asked to disable or stop it"),
	});
	pi.registerTool({
		name: "dynaflows",
		label: "Dynaflows",
		description: "Switch dynaflows between minimal mode (fixed guards only) and structured mode (the full workflow) for the " +
			"current repository and get the next step. Call it only when the user asked in this conversation to enable or " +
			"disable dynaflows or to switch its mode, in any wording; never on your own initiative.",
		parameters: dynaflowsToolParams,
		loadMode: "essential",
		approval: "write",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			if (loadError) return toolReply(loadError);
			if (ctx.agent.kind !== "main") return toolReply("Agent rails: only the main agent can switch the dynaflows mode.");
			const result = await applyDynaflows(ctx, dynaflowsToolParams.parse(params).mode, false);
			return toolReply(result.content ?? result.notice);
		},
	});
	pi.registerCommand("extension-health-dynaflows", {
		description: "Dynaflows registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Dynaflows registered", loadError ? "error" : "info"); },
	});
}
