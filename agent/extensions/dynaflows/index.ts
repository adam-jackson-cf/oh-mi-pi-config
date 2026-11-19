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
	type Mapping, type RepoRecord, type State as SharedState, type ToolEvent,
} from "../../lib/rails-runtime.ts";

const prCiSchema = z.object({
	headRefOid: z.string().regex(/^[0-9a-f]{40}$/),
	url: z.string().regex(/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/\d+$/),
	statusCheckRollup: z.array(z.union([
		z.object({ __typename: z.literal("CheckRun"), name: z.string(), status: z.string(), conclusion: z.union([z.string(), z.literal(null)]) }),
		z.object({ __typename: z.literal("StatusContext"), context: z.string(), state: z.string() }),
	])),
});

// Harness variables the omp runtime sets for its own commands. Rails strip them from every repo
// command they run (path check, checks.commit, harness checks) so repos never see the harness.
const harnessVariables = ["AGENT"];
// Characters of a failed commit check's output shown to the agent (the tail, where findings end).
const commitOutputLimit = 8000;
// Derived from this file's location so the rails hold no machine path: <omp>/scripts/ci-runner.py.
const ciRunnerPath = fileURLToPath(new URL("../../../scripts/ci-runner.py", import.meta.url));
function shellWord(value: string): string {
	return /^[\w@%+=:,./-]+$/.test(value) ? value : `'${value.replaceAll("'", "'\\''")}'`;
}

function prSelector(args: string[]): string | undefined {
	for (let i = 2; i < args.length; i++) {
		if (args[i] === "--") return args[i + 1];
		if (!args[i].startsWith("-")) return args[i];
		if (!args[i].includes("=") && !["--auto", "--admin", "--delete-branch", "-d", "--squash", "-s", "--merge", "-m", "--rebase", "-r", "--help"].includes(args[i])) i++;
	}
}
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
}

// Dynaflows is on for a repo only when its per-repo record (resolved from the origin remote under
// `configRoot`/repos/) says dynaflows.enabled true. `machineDynaflowsPath` (~/.omp/dynaflows.json)
// is the machine default: {"enabled": false} turns dynaflows off for every repo on this machine;
// absent or true defers to the record. No origin or no record means off. `configPath` is the rails
// mapping file (agent/agent-rails.yml shared with generic rails).
export default function dynaflows(
	pi: ExtensionAPI,
	{
		machineDynaflowsPath = fileURLToPath(new URL("../../../dynaflows.json", import.meta.url)),
		configPath = fileURLToPath(new URL("../../agent-rails.yml", import.meta.url)),
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
		loadError = `Agent rails: cannot load agent-rails.yml: ${String(error)}`;
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
	async function gitValue(cwd: string, args: string[]): Promise<string> {
		const result = await pi.exec("git", args, { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code !== 0 || result.killed || !result.stdout.trim()) throw new Error(`cannot resolve Git scope: ${result.stderr}`);
		return result.stdout.trim();
	}
	async function primaryGithubRepository(cwd: string): Promise<string> {
		const remotes = (await gitValue(cwd, ["remote"])).split("\n");
		const remote = remotes.includes("origin") ? "origin" : remotes.length === 1 ? remotes[0] : undefined;
		if (!remote) throw new Error("cannot resolve primary GitHub remote");
		const url = await gitValue(cwd, ["remote", "get-url", remote]);
		const match = /^(?:https:\/\/github\.com\/|git@github\.com:|ssh:\/\/git@github\.com\/)([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+?)(?:\.git)?$/.exec(url);
		if (!match) throw new Error("primary remote is not a validated GitHub repository");
		return match[1];
	}
	async function recordAt(root: string): Promise<RepoRecord | undefined> {
		return readRecord(root, configRoot);
	}
	async function gateAt(root: string, state: State, ctx: ExtensionContext, strict = false): Promise<Gate | undefined> {
		try {
			return (await recordAt(root))?.gate;
		} catch (error) {
			if (strict) throw error;
			note(state, ctx, `Agent rails: ${error instanceof Error ? error.message : String(error)}; reference checks apply.`);
		}
	}
	// Runs a repo command with the harness variables removed from its environment.
	function repoExec(argv: string[], cwd: string, timeout?: number) {
		return pi.exec("env", [...harnessVariables.flatMap(name => ["-u", name]), "--", ...argv], { cwd, timeout });
	}
	// prove()'s result file <git-common-dir>/omp-prove/<head>.result.json is the one pass signal:
	// it must say status pass for exactly `head`.
	async function proveResultReason(cwd: string, head: string): Promise<string | undefined> {
		const common = await pi.exec("git", ["rev-parse", "--git-common-dir"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (common.code !== 0 || common.killed) throw new Error("cannot resolve Git common directory for the prove() result");
		const path = resolve(cwd, common.stdout.trim(), "omp-prove", `${head}.result.json`);
		const result = z.object({ head: z.literal(head), status: z.literal("pass") });
		try {
			if (result.safeParse(JSON.parse(await readFile(path, "utf8"))).success) return;
		} catch { /* A missing or corrupt result is not a pass. */ }
		return `Agent rails: no prove() pass for ${head} at ${path}; run prove() for this head, then merge.`;
	}
	// Structure review must run even while all decisions remain provisional.
	async function structureResultReason(cwd: string, head: string): Promise<string | undefined> {
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
	type PrCi = { head?: string; repository?: string; reason?: string };
	// Reads the PR's exact head and whether its CI is green there.
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
		const head = pr.headRefOid;
		const repository = new URL(pr.url).pathname.split("/").slice(1, 3).join("/");
		if (selector && /^https?:\/\//i.test(selector) &&
			(await primaryGithubRepository(root)).toLowerCase() !== repository.toLowerCase()) {
			return { head, repository, reason: `Agent rails: run from a checkout of ${repository}` };
		}
		const gate = await gateAt(root, state, ctx, true);
		const checks = pr.statusCheckRollup;
		const stateOf = (check: typeof checks[number]) => check.__typename === "StatusContext"
			? check.state : `${check.status}/${check.conclusion ?? ""}`;
		const green = (check: typeof checks[number]) => check.__typename === "StatusContext"
			? check.state === "SUCCESS" : check.status === "COMPLETED" && check.conclusion === "SUCCESS";
		if (gate?.requiredCi) {
			const { requiredContext, runner } = gate.requiredCi;
			const named = checks.filter(check => (check.__typename === "StatusContext" ? check.context : check.name) === requiredContext);
			if (!named.some(green)) {
				const command = (verb: string) => `python3 ${shellWord(ciRunnerPath)} ${verb} ${shellWord(runner)} ${repository} ${head}`;
				return { head, repository, reason:
					`Agent rails: required CI context "${requiredContext}" is ${named.length ? stateOf(named[0]) : "missing"} for ${head} in ${repository} (runner ${runner}). ` +
					`Submit and wait: ${command("submit")}, then ${command("wait")} --context ${shellWord(requiredContext)}; ` +
					"see skill://git-tool/references/merge-and-recover.md for failures." };
			}
			// A declared context alone decides; other reported checks (optional apps, previews) never block.
			return { head, repository };
		}
		// No ci block declared: every reported check must be green.
		if (!checks.length) {
			const workflows = await stat(resolve(root, ".github/workflows")).catch(absentFile);
			return { head, repository, reason: gate?.ciCheck || workflows?.isDirectory()
				? `Agent rails: no CI checks reported for ${head}` : undefined };
		}
		const nonGreen: string[] = [];
		for (const check of checks) {
			const name = check.__typename === "StatusContext" ? check.context : check.name;
			if (check.__typename === "StatusContext" ? check.state !== "SUCCESS"
				: check.status !== "COMPLETED" || !["SUCCESS", "NEUTRAL", "SKIPPED"].includes(check.conclusion ?? "")) {
				nonGreen.push(`${name}: ${stateOf(check)}`);
			}
		}
		return { head, repository, reason: nonGreen.length || !checks.some(green)
			? `Agent rails: CI is not green for ${head}: ${nonGreen.join(", ") || "no SUCCESS check reported"}` : undefined };
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
	}
	// Whether dynaflows is on at `cwd`; when off, `off` says why in offText's words (the slug or
	// "none", then the reason). Strict callers throw on an invalid or refused record.
	async function dynaflowsAt(ctx: ExtensionContext, cwd = ctx.cwd, strict = false): Promise<{ on: boolean; off?: string; invalid?: boolean }> {
		const state = stateFor(ctx);
		const root = await repoRoot(cwd);
		const resolved = root ? await resolveRecord(root, configRoot) : { slug: null, dir: null, reason: "not in a Git repository" };
		if ((await machineDynaflows(state, ctx))?.enabled === false) {
			return { on: false, off: offText(resolved.slug, `the machine default ${machineDynaflowsPath} sets enabled false`) };
		}
		if (!root || !resolved.dir) return { on: false, off: offText(resolved.slug, resolved.reason ?? "no per-repo record") };
		try {
			const record = await recordAt(root);
			if (!record) return { on: false, off: offText(resolved.slug, `no per-repo record at ${join(resolved.dir, GATE_FILE)}`) };
			if (record.gate.dynaflows.enabled) return { on: true };
			return { on: false, off: offText(resolved.slug, `the record ${join(record.dir, GATE_FILE)} sets dynaflows.enabled false`) };
		} catch (error) {
			if (strict) throw error;
			return { on: false, invalid: true, off: offText(resolved.slug, error instanceof Error ? error.message : String(error)) };
		}
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
	// Sets the record's switch; it never creates a record or touches the repo.
	async function setDynaflows(dir: string, enabled: boolean): Promise<string> {
		const path = join(dir, GATE_FILE);
		const gate = z.record(z.string(), z.unknown()).parse(JSON.parse(await readGateText(dir)));
		gate.dynaflows = { enabled };
		const temp = `${path}.${randomUUID()}.tmp`;
		await writeFile(temp, `${JSON.stringify(gate, null, 2)}\n`);
		await rename(temp, path);
		return path;
	}
	async function checkPaths(root: string, changed: string[], state: State): Promise<string | undefined> {
		try {
			const canonicalRoot = await realpath(root);
			const candidates: string[] = [];
			for (const path of changed) {
				try {
					await lstat(path);
					const canonical = await realpath(path);
					const rel = relative(canonicalRoot, canonical);
					// A repository symlink escaping its root is still a repository path to check.
					candidates.push(rel === ".." || rel.startsWith("../") || isAbsolute(rel)
						? resolve(canonicalRoot, relative(root, path)) : canonical);
				} catch (error) {
					const parsed = fileErrorSchema.safeParse(error);
					if (!parsed.success || parsed.data.code !== "ENOENT") throw error;
				}
			}
			changed = [...new Set(candidates)];
			const status = await pi.exec("git", ["status", "--porcelain=v1", "-z", "--untracked-files=all"], { cwd: canonicalRoot, timeout: SUBPROCESS_TIMEOUT_MS });
			if (status.code !== 0 || status.killed) {
				throw new Error(`git status failed${status.killed ? " (killed)" : ` (exit ${status.code})`}: ${status.stderr || status.stdout}`.trim());
			}
			const changedPaths = new Set<string>();
			const records = status.stdout.split("\0");
			for (let index = 0; index < records.length; index++) {
				const record = records[index];
				if (!record) continue;
				if (record.slice(0, 2) === "??" || /[AR]/.test(record.slice(0, 2))) changedPaths.add(record.slice(3));
				// In NUL-delimited porcelain, rename/copy destinations precede their source path.
				if (record.slice(0, 2).includes("R") || record.slice(0, 2).includes("C")) index++;
			}
			changed = changed.filter(path => changedPaths.has(relative(canonicalRoot, path)));
			if (!changed.length) { state.pathFailures.delete(root); return; }
			const record = await recordAt(root);
			if (!record) throw new Error("per-repo record disappeared during path check");
			const result = await repoExec([...harnessPathCommand, "--repo", canonicalRoot, "--record", record.dir, "--event", "edit", ...changed], canonicalRoot, SUBPROCESS_TIMEOUT_MS);
			if (result.code !== 0 || result.killed) throw new Error(`${result.stdout}\n${result.stderr}`.trim());
			state.pathFailures.delete(root);
		} catch (error) {
			state.pathFailures.set(root, changed);
			return `Agent rails: path check failed in ${root}: ${String(error)}`;
		}
	}
	// The record's ownerOnlyPaths are the owner's alone (for example the dependency-audit exception
	// file). At every agent commit in a dynaflows-on repo with a record, rails
	// refuse the commit when a staged or unstaged tracked change touches one. Renames count as a
	// deletion and an addition, so moving an owner-only file away is refused too.
	async function ownerOnlyReason(root: string): Promise<string | undefined> {
		const record = await recordAt(root);
		if (!record || !record.gate.ownerOnlyPaths.length) return;
		const touched = new Set<string>();
		for (const args of [["diff", "--cached", "--name-only", "--no-renames", "-z"], ["diff", "--name-only", "--no-renames", "-z"]]) {
			const result = await pi.exec("git", args, { cwd: root, timeout: SUBPROCESS_TIMEOUT_MS });
			if (result.code !== 0 || result.killed) throw new Error(`git ${args.join(" ")} failed: ${result.stderr}`.trim());
			for (const path of result.stdout.split("\0")) if (path && ownerOnly(path, record.gate.ownerOnlyPaths)) touched.add(path);
		}
		if (!touched.size) return;
		const paths = [...touched].sort();
		return `Agent rails: this commit changes owner-only ${paths.length === 1 ? "path" : "paths"} ${paths.join(", ")} ` +
			`(the record's ownerOnlyPaths in ${join(record.dir, GATE_FILE)}). Agents never change these paths, such as the ` +
			"dependency-audit exception file. Restore them (git restore --staged --worktree -- <path>), then stop and raise " +
			"the change to the owner: what you need changed, why, and the evidence. The owner makes the change.";
	}
	async function commitChecksReason(root: string): Promise<string | undefined> {
		const record = await recordAt(root);
		if (!record) return;
		const pathResult = await repoExec([...harnessPathCommand, "--repo", root, "--record", record.dir, "--event", "staged"], root, SUBPROCESS_TIMEOUT_MS);
		if (pathResult.code !== 0 || pathResult.killed) {
			return `Agent rails: staged path check failed in ${root}: ${pathResult.stdout}\n${pathResult.stderr}\nFix the findings, then commit again.`;
		}
		const runs: [string, string[]][] = [
			[`checks.commit (${record.gate.commit.join(" ")})`, record.gate.commit],
			["harness checks (guardrails)", [...guardrailsCommand, "--repo", root, "--record", record.dir]],
		];
		for (const [label, argv] of runs) {
			const result = await repoExec(argv, root);
			if (result.code === 0 && !result.killed) continue;
			const output = `${result.stdout}\n${result.stderr}`.trim();
			const owned = argv === record.gate.commit ? "" : `\n${harnessOnlyNote}`;
			return `Agent rails: ${label} failed in ${root}${result.killed ? " (killed)" : ` (exit ${result.code})`}; ` +
				`fix every finding in the repo, then commit again:\n${output.length > commitOutputLimit ? `…${output.slice(-commitOutputLimit)}` : output}${owned}`;
		}
	}
	function recordRead(event: ToolEvent, state: State): void {
		if (event.toolName !== "read" || event.isError || event.input.path === undefined) return;
		const url = event.input.path.replace(/:(?:raw|[\d,+-]+)(?::(?:raw|[\d,+-]+))*$/, "");
		if (url.startsWith("skill://")) state.reads.add(url);
	}
	async function recordResult(event: ToolEvent, ctx: ExtensionContext, state: State, runCheck: boolean) {
		recordRead(event, state);
		const details = event.details;
		if (event.isError) return;
		// ast_edit is preview-only; omp://tools/ast-edit.md applies it via write xd://resolve.
		if (event.toolName === "ast_edit" && details.applied !== true) {
			state.proposal = details.files?.flatMap(path => absolute(path, ctx.cwd) ?? []) ?? [];
			return;
		}
		if (event.toolName === "write" && event.input.path === map.rejectDevice) {
			state.proposal = [];
			return;
		}
		if (!map.editTools.includes(event.toolName)) return;
		let changed = paths(event, map, ctx.cwd);
		if (event.toolName === "write" && event.input.path === map.resolveDevice) {
			changed = details.applied === true && details.files
				? details.files.flatMap(path => absolute(path, ctx.cwd) ?? []) : [];
			state.proposal = [];
		}
		const groups = new Map<string, string[]>();
		for (const path of changed) {
			const root = await repoRoot(path);
			if (!root) continue;
			if (matches(relative(root, path), map.codeGlobs)) state.edits.add(root);
			const group = groups.get(root) ?? [];
			group.push(path); groups.set(root, group);
		}
		const errors: string[] = [];
		if (runCheck) for (const [root, changedPaths] of groups) {
			if (!await dynaflowsOn(ctx, root)) continue;
			const changed = [...new Set([...(state.pathFailures.get(root) ?? []), ...changedPaths])];
			const error = await checkPaths(root, changed, state);
			if (error) errors.push(error);
		}
		return errors.length ? { additionalContext: errors.join("\n") } : undefined;
	}
	async function rebuild(_event: SessionStartEvent | SessionSwitchEvent | SessionBranchEvent | SessionTreeEvent, ctx: ExtensionContext): Promise<void> {
		const state = fresh();
		const previous = states.get(ctx.sessionManager.getSessionId());
		state.pointed = previous?.pointed ?? false;
		state.pathFailures = previous?.pathFailures ?? new Map();
		states.set(ctx.sessionManager.getSessionId(), state);
		if (loadError) { note(state, ctx, loadError); return; }
		const calls = new Map<string, ToolEvent>();
		for (const entry of ctx.sessionManager.getBranch()) {
			if (entry.type === "compaction") { state.reads.clear(); continue; }
			if (entry.type !== "message") continue;
			const message = entry.message;
			if (message.role === "assistant") for (const block of message.content) {
				if (block.type !== "toolCall") continue;
				calls.set(block.id, policyEvent({ type: "tool_call", toolName: block.name, toolCallId: block.id, input: block.arguments }));
			}
			// Persisted roles use camelCase, not the extension event name (omp://session.md).
			if (message.role === "toolResult") {
				const call = calls.get(message.toolCallId);
				if (call) await recordResult({ ...call, isError: message.isError, details: detailsSchema.parse(message.details) }, ctx, state, false);
			}
		}
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
	pi.on("tool_call", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { block: true, reason: loadError };
		const state = stateFor(ctx);
		try {
			if (event.toolName === "bash" && event.input.command !== undefined) {
				const cwd = absolute(event.input.cwd ?? ctx.cwd, ctx.cwd) ?? ctx.cwd;
				for (const action of await gitActions(event.input.command, cwd)) {
					if (!new RegExp(map.gitCommand).test(action.command)) continue;
					if (/^git commit\b/.test(action.command)) {
						if (!action.cwd) return {
							block: true, reason: "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.",
						};
						const root = await repoRoot(action.cwd);
						const at = root ? await dynaflowsAt(ctx, root, true) : await dynaflowsAt(ctx, action.cwd);
						const owned = root && at.on ? await ownerOnlyReason(root) : undefined;
						if (owned) return { block: true, reason: owned };
						// Fail-open is never silent: every commit outside dynaflows says why.
						if (!at.on && at.off) ctx.ui.notify(`Agent rails: ${at.off}; commit checks do not run.`, "info");
						if (root && at.on) {
							const reason = await commitChecksReason(root);
							if (reason) return { block: true, reason };
						}
					}
					if (/^gh pr merge\b/.test(action.command)) {
						if (!action.cwd) throw new Error("cannot resolve PR action cwd");
						// Dynaflows merges require exact-head CI, prove pass and a non-halting structure result.
						if (await dynaflowsOn(ctx, action.cwd)) {
							const args = action.args;
							const at = args.findIndex(arg => arg === "--match-head-commit" || arg.startsWith("--match-head-commit="));
							const head = at < 0 ? undefined : args[at].includes("=") ? args[at].slice(args[at].indexOf("=") + 1) : args[at + 1];
							if (at < 0 || !head) return { block: true, reason: "Agent rails: merge requires --match-head-commit <40-hex verified SHA>" };
							if (/[$`]/.test(head)) return {
								block: true, reason: `Agent rails: received unexpanded shell text \`${head}\`; rails check the literal command, so pass the 40-hex SHA itself`,
							};
							if (!/^[0-9a-f]{40}$/.test(head)) return { block: true, reason: `Agent rails: --match-head-commit must be a lowercase 40-hex SHA, got ${head}` };
							const proved = await proveResultReason(action.cwd, head);
							if (proved) return { block: true, reason: proved };
							const structure = await structureResultReason(action.cwd, head);
							if (structure) return { block: true, reason: structure };
							const pr = await prCi(action.cwd, prSelector(action.args), state, ctx);
							if (pr.head && pr.head !== head) return {
								block: true, reason: `Agent rails: PR head is ${pr.head}, not ${head}; prove() the current head`,
							};
							if (pr.reason) return { block: true, reason: pr.reason };
						}
					}
				}
			}
			if (!map.editTools.includes(event.toolName)) return;
			const targets = event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
			const required = new Set<string>();
			for (const path of targets) {
				const root = await repoRoot(path);
				// Targeted policy reads belong only to this path's dynaflows-on repository.
				if (!root || !await dynaflowsOn(ctx, root)) continue;
				const rel = relative(root ?? ctx.cwd, path);
				const triggers: string[] = [];
				if (!state.edits.has(root) && matches(rel, map.codeGlobs)) triggers.push("firstEdit");
				// New-file reads apply only to code files inside a repository, not scratch paths.
				if (root && matches(rel, map.codeGlobs) && !(await access(path).then(() => true, () => false))) triggers.push("newFile");
				if (matches(rel, map.testGlobs)) triggers.push("test");
				for (const url of missing(state, map, triggers)) required.add(url);
			}
			if (required.size) return { block: true, reason: readReason([...required]) };
		} catch (error) { return { block: true, reason: `Agent rails: policy could not be evaluated: ${String(error)}` }; }
	});
	pi.on("tool_result", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { additionalContext: loadError };
		try { return await recordResult(event, ctx, stateFor(ctx), true); }
		catch (error) { return { additionalContext: `Agent rails: post-edit policy failed: ${String(error)}` }; }
	});
	pi.on("session_stop", async (_event, ctx) => {
		if (loadError) return { decision: "block", reason: loadError };
		const state = stateFor(ctx);
		try {
			const reasons: string[] = [];
			const roots = new Set([...state.edits, ...state.pathFailures.keys()]);
			let editedDynaflowsRepo = false;
			for (const root of roots) {
				if (!await dynaflowsOn(ctx, root)) continue;
				if (state.edits.has(root)) editedDynaflowsRepo = true;
				const failedPaths = state.pathFailures.get(root);
				if (failedPaths) {
					const error = await checkPaths(root, failedPaths, state);
					if (error) reasons.push(error);
				}
			}
			// Repository checks run at commit (pre-commit hook) and in CI, never at handoff.
			const urls = editedDynaflowsRepo ? missing(state, map, ["verification"]) : [];
			if (urls.length) reasons.push(`read ${urls.join(" and ")}`);
			if (reasons.length) {
				return { decision: "block", reason: `Agent rails: ${reasons.join("; ")}, then retry handoff.` };
			}
		} catch (error) { return { decision: "block", reason: `Agent rails: handoff policy failed: ${String(error)}` }; }
	});
	// Applies the owner's dynaflows ask for the current repo: sets the record's switch, grants (or, on
	// off, revokes) this session's record writes, and returns the next step for the agent, or none.
	// `task` is true when the prompt also holds work to route.
	async function applyDynaflows(ctx: ExtensionContext, enabled: boolean, task: boolean): Promise<{ notice: string; content?: string }> {
		const state = stateFor(ctx);
		const notify = (notice: string, level: "info" | "error" = "info") => { ctx.ui.notify(notice, level); return notice; };
		let target: { slug: string; dir: string | null };
		try { target = await directiveTarget(ctx); }
		catch (error) { return { notice: notify(`Agent rails: ${String(error)}`, "error") }; }
		state.pointed = false;
		const { slug, dir } = target;
		const recordPath = recordDir(slug, configRoot);
		if (!dir) {
			if (!enabled) return { notice: notify(`Agent rails: ${slug} has no per-repo record, so dynaflows is already off.`) };
			// No record yet: setup starts at Step 0 and the agent writes the record, switch on, itself.
			return { notice: notify(`Agent rails: ${slug} has no per-repo record; starting repository setup.`), content:
				`The owner asked to enable dynaflows for ${slug}, which has no per-repo record yet. ` +
				"Start skill://dynaflows/references/harness-setup.md setup now, from Step 0. Write the record " +
				`yourself at ${recordPath} as setup reaches each file, with gate.json setting ` +
				'"dynaflows": {"enabled": true}, so dynaflows is on once the record is complete. Never ask the owner to save a file.' };
		}
		const resume = (reason: string) => `The owner asked to enable dynaflows for ${slug}, but its per-repo record ` +
			`${join(dir, GATE_FILE)} is refused: ${reason}. Resume skill://dynaflows/references/harness-setup.md setup now and repair ` +
			'the record yourself in place, with "dynaflows": {"enabled": true}. ' +
			"Never ask the owner to save a file.";
		let notice: string;
		try {
			notice = notify(`Agent rails: dynaflows ${enabled ? "on" : "off"} (${await setDynaflows(dir, enabled)}).`);
		} catch (error) {
			const reason = error instanceof Error ? error.message : String(error);
			notice = notify(`Agent rails: cannot set dynaflows in ${join(dir, GATE_FILE)}: ${reason}`, "error");
			return { notice, content: enabled ? resume(reason) : undefined };
		}
		if (!enabled) return { notice, content: "Dynaflows off: do not apply dynaflows." };
		const at = await dynaflowsAt(ctx);
		if (at.invalid) return { notice, content: resume(at.off ?? "invalid record") };
		if (!at.on) {
			const off = sentence(at.off ?? "dynaflows off");
			return { notice: notify(`Agent rails: ${off}`), content: `${off.charAt(0).toUpperCase()}${off.slice(1)} Do not apply dynaflows.` };
		}
		state.pointed = true;
		if (task) return { notice, content: `Dynaflows on. Route this work with ${map.references.workflow.join(" and ")}.` };
		return { notice, content: `Dynaflows on for ${slug}. The owner asked to start dynaflows here, so begin now: read ` +
			`${map.references.workflow.join(" and ")} and assess this repo against its Start here playbook selection ` +
			"(Project setup when controls, the Verification surface or the feature map are missing). Report the stage you found " +
			"and start it. If the owner's message also names a task, route that task; if setup is complete and no task was " +
			"named, report that dynaflows is ready and ask the owner for the task." };
	}
	pi.on("before_agent_start", async (event, ctx) => {
		if (loadError || ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const state = stateFor(ctx);
		const message = (content: string) => ({ message: { customType: "agent-rails-dynaflows", content, display: false, attribution: "agent" as const } });
		const lines = (event.prompt ?? "").split("\n").filter(line => line.trim());
		const settings = lines.map(dynaflowsSetting);
		const asked = new Set(settings.filter(setting => setting !== undefined));
		if (asked.size > 1) {
			ctx.ui.notify("Agent rails: contradictory dynaflows directives; no setting changed.", "error");
			return message("The owner's message asks to both enable and disable dynaflows, so no setting changed. " +
				"Ask the owner which they want, then call the dynaflows tool with their answer.");
		}
		if (asked.size) {
			// A line with no directive is the task to route.
			const { content } = await applyDynaflows(ctx, asked.has(true), settings.some(setting => setting === undefined));
			return content === undefined ? undefined : message(content);
		}
		if (state.pointed || !(await dynaflowsOn(ctx))) return;
		state.pointed = true;
		return message(`Dynaflows on. Route this work with ${map.references.workflow.join(" and ")}.`);
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
			const reply = (text: string) => ({ content: [{ type: "text" as const, text }], details: {} });
			if (loadError) return reply(loadError);
			if (ctx.agent.kind !== "main") return reply("Agent rails: only the main agent can toggle dynaflows.");
			const result = await applyDynaflows(ctx, dynaflowsToolParams.parse(params).enabled, false);
			return reply(result.content ?? result.notice);
		},
	});
	pi.registerCommand("extension-health-dynaflows", {
		description: "Dynaflows registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Dynaflows registered", loadError ? "error" : "info"); },
	});
}
