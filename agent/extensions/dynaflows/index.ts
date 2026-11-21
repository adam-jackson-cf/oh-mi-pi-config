import { access, lstat, readFile, realpath } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import {
	z, type ExtensionAPI, type ExtensionContext,
	type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent,
} from "@oh-my-pi/pi-coding-agent";
import {
	configRoot as defaultConfigRoot, GATE_FILE, type Gate, loadGate, minimalText, recordDir, repoSlug, resolveRecord,
} from "./lib/repo-record.ts";
import { defaultModesPath, MODES, type Mode, repoMode, writeMode } from "./lib/modes.ts";
import {
	SUBPROCESS_TIMEOUT_MS, stringList, loadMapping, detailsSchema, policyEvent, absolute, repoRoot,
	paths, pathArguments, matches, missing, sentence, readReason, gitActions, readRecord, note, fileErrorSchema,
	codeRunsGit, INDEX_WRITES, commitScopeArgument,
	recordRead, subagentPathReason, type GitAction, type Mapping, type RepoRecord, type State as SharedState, type ToolEvent,
} from "./lib/rails-runtime.ts";
import {
	PUBLISHES, adminMergeReason, agentMergeReason, commitHookReason, configWriteReason, hookBypassReason, mergeMethodReason, mergePolicyFor, minimalMergeReason,
	prMerge, prMergeReason, pushReason, routeReason, worktreeReason, type DefaultBranches,
} from "./lib/guards.ts";
import {
	defaultJudgmentsDir, defaultReviewsPath, judgmentReady, ompJudge, registryDecide, scopeInputSchema, sessionRails, type JudgeTransport, type ObligationSource,
	type PrStatus,
} from "./lib/session-rails.ts";
import {
	COMMIT_CHECKS_TIMEOUT_MS, Evidence, Snapshot, evalReason, fileState, pinSettings, protectedPathReason, setting as ompSetting, shellStateReason,
	type Protection,
} from "./lib/trust.ts";

const prCiSchema = z.object({
	headRefOid: z.string().regex(/^[0-9a-f]{40}$/),
	baseRefName: z.string().min(1),
	headRefName: z.string().min(1),
	state: z.string().min(1),
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
// github tool operations that push, open PRs or move local branches.
const GITHUB_TOOL_WRITES = ["pr_create", "pr_push", "pr_checkout"];
// Characters of a failed commit check's output shown to the agent (the tail, where findings end).
const commitOutputLimit = 8000;
// Derived from this file's location so the rails hold no machine path: <omp>/scripts/ci_runner.py.
const ciRunnerPath = fileURLToPath(new URL("../../../scripts/ci_runner.py", import.meta.url));
function shellWord(value: string): string {
	return /^[\w@%+=:,./-]+$/.test(value) ? value : `'${value.replaceAll("'", "'\\''")}'`;
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
	// The owner's latest prompt named dynaflows, so the dynaflows tool may switch to minimal mode.
	ownerAsked: boolean;
}
function fresh(): State {
	return {
		reads: new Set(), notes: new Set(), edits: new Set(), pathFailures: new Map(),
		pointed: false, proposal: [], ownerAsked: false,
	};
}
// One bash call as the rails saw it: every git/gh action and every executable it runs.
type BashCall = { actions: GitAction[]; executables: string[] };
const COMMIT_COMPANIONS = ["cd", "pushd", "popd", "true"];
const READ_ONLY_GIT = /^git (?:status|diff|log|show|rev-parse)\b/;
// The harness checks: guardrails run from the harness's own reference project (never installed
// into the repo), over the whole repo, with the record as config. Rails append
// `--repo <worktree> --record <record dir>`.
const guardrailsProject = fileURLToPath(new URL("./engine", import.meta.url));
const defaultGuardrailsCommand = ["uv", "run", "--project", guardrailsProject, "guardrails", "check"];
const harnessPathCommand = ["uv", "run", "--project", guardrailsProject, "guardrails", "path-check"];
const defaultProfileCommand = ["uv", "run", "--project", guardrailsProject, "guardrails", "profile-check"];
// The judgment registry (engine/workflows.py) as the rails call it: questions, then record.
const defaultDecideCommand = ["uv", "run", "--project", guardrailsProject, "python", fileURLToPath(new URL("./lib/decide.py", import.meta.url))];
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
type Schema = Pick<ReturnType<typeof z.object>, "safeParse">;
function parsesAs(schema: Schema, text: string): boolean {
	try { return schema.safeParse(JSON.parse(text)).success; } catch { return false; }
}
async function commonDir(pi: ExecApi, cwd: string): Promise<string> {
	const common = await pi.exec("git", ["rev-parse", "--git-common-dir"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (common.code !== 0 || common.killed || !common.stdout.trim()) throw new Error("cannot resolve Git common directory for proof evidence");
	return resolve(cwd, common.stdout.trim());
}
const untrustedEvidence = (what: string, head: string, path: string) =>
	`Agent rails: the ${what} result for ${head} at ${path} was not written by a ${what} call the rails saw in this omp process; run ${what} for this head here, then merge.`;
async function proveResultReason(pi: ExecApi, cwd: string, head: string, evidence: Evidence): Promise<string | undefined> {
	const path = join(await commonDir(pi, cwd), "omp-prove", `${head}.result.json`);
	const result = z.object({ head: z.literal(head), status: z.literal("pass") });
	const text = await readFile(path, "utf8").catch(() => "");
	if (!parsesAs(result, text)) return `Agent rails: no prove() pass for ${head} at ${path}; run prove() for this head, then merge.`;
	return evidence.trusts(path, text) ? undefined : untrustedEvidence("prove()", head, path);
}
async function structureResultReason(pi: ExecApi, cwd: string, head: string, evidence: Evidence): Promise<string | undefined> {
	const path = join(await commonDir(pi, cwd), "omp-structure", `${head}.json`);
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
	let text = "";
	try { text = await readFile(path, "utf8"); result = schema.parse(JSON.parse(text)); }
	catch { return `Agent rails: no valid structure_review() result for ${head} at ${path}; run structure_review() for this head, then merge.`; }
	if (!evidence.trusts(path, text)) return untrustedEvidence("structure_review()", head, path);
	if (result.status === "incomplete" || result.errors?.length) {
		return `Agent rails: structure_review() for ${head} is incomplete (${result.errors?.join("; ") || "status incomplete"}); fix the cause and rerun it.`;
	}
	if (!result.halt) return;
	const ready = result.findings.filter(item => item.status === "ready").map(item => `${item.unit}: ${item.question}`);
	return `Agent rails: ready structure findings block ${head}: ${ready.join("; ") || "halt is true"}; fix them and rerun prove() and structure_review() on the new head.`;
}
type PrCi = { reason: string } | { head: string; repository: string; base: string; headRef: string; state: string; gate: Gate | undefined; ciReason: string | undefined };
// The turn-end status of the agent's PR: open is unmet while agents may merge; pending or failing CI
// and an unreadable PR are unmet with their reason, so the turn waits for CI instead of settling.
function prObligation(pr: PrCi): PrStatus {
	if (!("head" in pr)) return { status: "unmet", reason: pr.reason };
	if (pr.state !== "OPEN" || pr.gate?.agentMerge === "disabled") return { status: "met" };
	return { status: "unmet", reason: pr.ciReason };
}
type PrIdentity = { head: string; repository: string };
type PrState = Exclude<PrCi, { reason: string }>;
// The merge policy for the PR's base and head decides the method and whether proof is needed.
async function policyMergeReason(pi: ExecApi, args: string[], at: { cwd: string; head: string; evidence: Evidence }, pr: PrState): Promise<string | undefined> {
	const { cwd, head, evidence: watched } = at;
	const policy = mergePolicyFor(pr.gate?.mergePolicies ?? [], pr.base, pr.headRef);
	const method = mergeMethodReason(args, policy);
	if (method) return method;
	if (pr.head !== head) return `Agent rails: PR head is ${pr.head}, not ${head}; ${policy.proof ? "prove() the current head" : "merge the current head"}`;
	const evidence = policy.proof ? (await proveResultReason(pi, cwd, head, watched)) ?? (await structureResultReason(pi, cwd, head, watched)) : undefined;
	return evidence ?? pr.ciReason;
}
async function foreignRepository(pi: ExecApi, root: string, selector: string | undefined, repository: string): Promise<boolean> {
	if (!selector || !/^https?:[/]{2}/i.test(selector)) return false;
	return (await primaryGithubRepository(pi, root)).toLowerCase() !== repository.toLowerCase();
}
function requiredCiReason(checks: CheckReport[], required: { requiredContext: string; runner: string }, pr: PrIdentity): string | undefined {
	const { requiredContext, runner } = required;
	const named = checks.filter(check => check.name === requiredContext);
	if (named.length && named.every(check => check.green)) return undefined;
	const command = (verb: string) => `python3 ${shellWord(ciRunnerPath)} ${verb} ${shellWord(runner)} ${pr.repository} ${pr.head}`;
	return `Agent rails: required CI context "${requiredContext}" is ${named.find(check => !check.green)?.state ?? "missing"} for ${pr.head} in ${pr.repository} (runner ${runner}). ` +
		`Submit and wait: ${command("submit")}, then ${command("wait")} --context ${shellWord(requiredContext)}; ` +
		"see skill://git-tool/references/merge-and-recover.md for failures.";
}
function ciReason(checks: CheckReport[], gate: Gate | undefined, pr: PrIdentity): string | undefined {
	if (gate?.requiredCi) return requiredCiReason(checks, gate.requiredCi, pr);
	if (!checks.length) return `Agent rails: no CI checks reported for ${pr.head}; structured merges need CI on the exact head.`;
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
// Git's own commands (built-ins and the scripts it ships), plus git-lfs: any other subcommand is an
// alias the rails expand or an external program they refuse.
async function gitBuiltins(pi: ExecApi, cache: SessionCache, cwd: string): Promise<Set<string>> {
	cache.builtins ??= gitValue(pi, cwd, ["--list-cmds=builtins,main"]).then(text => new Set([...text.split("\n"), "lfs"]));
	return cache.builtins;
}
async function aliasActions(pi: ExecApi, cache: SessionCache, action: GitAction): Promise<GitAction[] | undefined> {
	const [name, ...rest] = action.args;
	if (!action.cwd || !action.command.startsWith("git ") || !name || name.startsWith("-")) return undefined;
	if ((await gitBuiltins(pi, cache, action.cwd)).has(name)) return undefined;
	const result = await pi.exec("git", ["config", "--get", `alias.${name}`], { cwd: action.cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.killed) throw new Error(`git config --get alias.${name} was killed`);
	const expansion = result.code === 0 ? result.stdout.trim() : "";
	if (!expansion) throw new Error(`git ${name} is neither a Git command nor an alias; external git-* programs run outside the rails`);
	const words = rest.map(shellWord).join(" ");
	if (expansion.startsWith("!")) return gitActions(`${expansion.slice(1)} ${words}`, action.cwd);
	const config = action.config.map(entry => `-c ${shellWord(entry)}`).join(" ");
	return gitActions(`git ${config} ${expansion} ${words}`, action.cwd);
}
async function subagentReason(map: Mapping, event: ToolEvent, ctx: ExtensionContext, state: State): Promise<string | undefined> {
	if (!map.editTools.includes(event.toolName) || ctx.agent.kind === "main") return undefined;
	return subagentPathReason([...pathArguments(event, map), ...(event.input.path === map.resolveDevice ? state.proposal : [])]);
}
function commitFailure(label: string, root: string, result: { code: number; killed?: boolean; stdout: string; stderr: string }, repoOwned: boolean): string {
	if (result.killed) return `Agent rails: ${label} did not finish within the commit rail's ${COMMIT_CHECKS_TIMEOUT_MS / 1000}s budget in ${root}; make checks.commit faster, then commit again.`;
	const output = `${result.stdout}\n${result.stderr}`.trim();
	return `Agent rails: ${label} failed in ${root} (exit ${result.code}); ` +
		`fix every finding in the repo, then commit again:\n${output.length > commitOutputLimit ? `…${output.slice(-commitOutputLimit)}` : output}${repoOwned ? "" : `\n${harnessOnlyNote}`}`;
}
// A GitHub token in a command lets any program call the API outside the rails.
function tokenReason(command: string): string | undefined {
	return /\$\{?(?:GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN)\b/.test(command)
		? "Agent rails: commands may not read the GitHub token; use gh and git directly so the rails check them."
		: undefined;
}
type Trust = { evidence: Evidence; snapshot: Snapshot; defaults: DefaultBranches; protection: Protection };
// The checks run before the command, so a commit must be the bash call's only change.
function committedTreeReason(action: GitAction, root: string, call: BashCall): string | undefined {
	const others = call.actions.filter(other => other !== action && !READ_ONLY_GIT.test(other.command));
	const commands = call.executables.filter(command => command !== "git" && !COMMIT_COMPANIONS.includes(command));
	return others.length || commands.length
		? `Agent rails: run git commit in ${root} as its own bash command (stage in an earlier call) so the commit checks see the tree being committed.`
		: undefined;
}
// checks.commit runs on the working tree, so it must match what git commit records.
async function unstagedReason(pi: ExecApi, action: GitAction, root: string): Promise<string | undefined> {
	if (action.args.slice(1).some(arg => arg === "--all" || /^-[^-]*a/.test(arg))) return undefined;
	const diff = await pi.exec("git", ["diff", "--name-only"], { cwd: root, timeout: SUBPROCESS_TIMEOUT_MS });
	if (diff.code !== 0 || diff.killed) throw new Error(`git diff failed: ${diff.stderr}`);
	return diff.stdout.trim()
		? `Agent rails: unstaged changes in ${root} would reach the commit checks but not the commit; stage or stash them first:\n${diff.stdout.trim()}`
		: undefined;
}
async function evalToolReason(trust: Trust, event: ToolEvent, cwd: string): Promise<string | undefined> {
	if (event.toolName !== "eval") return undefined;
	const code = event.input.code ?? "";
	const reason = evalReason(code, trust.protection);
	if (!reason) await trust.evidence.begin(event.toolCallId, code, cwd);
	return reason;
}
function protectedEditReason(map: Mapping, protection: Protection, event: ToolEvent, cwd: string, state: State): string | undefined {
	if (!map.editTools.includes(event.toolName)) return undefined;
	const targets = [...paths(event, map, cwd), ...(event.input.path === map.resolveDevice ? state.proposal : [])];
	for (const target of targets) {
		const reason = protectedPathReason(target, protection);
		if (reason) return reason;
	}
	return undefined;
}
// The rail state a session trusts: the mode file and the session repo's origin. Records are not
// part of it: agents write them at any time and the rails re-read them on every action (ADR 0001).
async function captureState(snapshot: Snapshot, modesFile: string, cwd: string): Promise<void> {
	await snapshot.capture(modesFile, () => fileState(modesFile));
	const root = await repoRoot(cwd);
	if (!root) return;
	await snapshot.capture(`the origin remote of ${root}`, async () => `${await repoSlug(root)}`);
}
function mergeAloneReason(actions: GitAction[]): string | undefined {
	return actions.length > 1 && actions.some(action => /^gh pr merge\b/.test(action.command))
		? "Agent rails: run gh pr merge as its own bash command, so the head the rails check is the head gh merges."
		: undefined;
}
type Notify = (notice: string, level?: "info" | "error") => string;
type Step = { action: GitAction; staged: boolean };
// The staged checks (handoff, commit checks, implementation-integrity) read the index before the command runs,
// so the committed tree must be that index.
function committedIndexReason(step: Step, mode: Mode): string | undefined {
	if (step.staged) return "Agent rails: run git commit in a command of its own after staging; the rails check the index as it is before the command runs.";
	const scope = mode === "structured" ? commitScopeArgument(step.action.args) : undefined;
	return scope === undefined ? undefined
		: `Agent rails: structured commits commit the staged index only; stage with git add, then run git commit without -a, -i, -o, -p or paths (got ${scope}).`;
}
// Git writes through tools other than bash cannot be checked: eval code that starts git or gh, github tool writes.
function otherToolReason(event: ToolEvent): string | undefined {
	if (event.toolName === "eval" && event.input.code !== undefined && codeRunsGit(event.input.code)) {
		return "Agent rails: eval code that runs git or gh cannot be checked; run Git and GitHub commands through bash.";
	}
	if (event.toolName === "github" && GITHUB_TOOL_WRITES.includes(event.input.op ?? "")) {
		return `Agent rails: the github tool's ${event.input.op} writes outside the Git rails; run git or gh through bash.`;
	}
	return undefined;
}
type StructuredRequest = { notice: string; slug: string; dir: string; task: boolean };

type DynaflowsOptions = {
	modesPath: string; configPath: string; configRoot: string; guardrailsCommand: string[]; guardrailsProfileCommand: string[];
	ompCommand: string[]; extensionDir: string; reviewsPath: string; obligationSources: ObligationSource[];
	decideCommand: string[];
	/** The judgments folder whose thresholds.json statuses decide which judgments the rails enforce. */
	judgmentsDir: string;
	/** The judge transport for judged rails; omp's judge role unless a test stubs it. */
	judge: ((ctx: ExtensionContext) => JudgeTransport) | undefined;
};
function defaultOptions(): DynaflowsOptions {
	return {
		modesPath: defaultModesPath(),
		configPath: fileURLToPath(new URL("./rails.yml", import.meta.url)),
		configRoot: defaultConfigRoot(),
		guardrailsCommand: defaultGuardrailsCommand,
		guardrailsProfileCommand: defaultProfileCommand,
		ompCommand: ["omp"],
		extensionDir: fileURLToPath(new URL(".", import.meta.url)),
		// The omp agent dir, by the same rule as the judgment call log; never a repository.
		reviewsPath: defaultReviewsPath(),
		obligationSources: [],
		decideCommand: defaultDecideCommand,
		judgmentsDir: defaultJudgmentsDir(),
		judge: undefined,
	};
}

export default function dynaflows(pi: DynaflowsApi, options: Partial<DynaflowsOptions> = {}): void {
	const {
		modesPath, configPath, configRoot, guardrailsCommand, guardrailsProfileCommand, ompCommand, extensionDir, reviewsPath, obligationSources,
		decideCommand, judge, judgmentsDir,
	}: DynaflowsOptions = { ...defaultOptions(), ...options };
	const states = new Map<string, State>(), session: SessionCache = {}, trust: Trust = {
		evidence: new Evidence(), snapshot: new Snapshot(), defaults: new Map(),
		protection: {
			modesFile: modesPath, reviewsFile: reviewsPath, extensionDir: dirname(configPath),
			harnessConfig: resolve(dirname(configPath), "../../config.yml"),
		},
	};
	let map: Mapping;
	let loadError: string | undefined;
	try {
		map = loadMapping(configPath);
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
	const sessionRail = sessionRails(pi, {
		configRoot, extensionDir, reviewsPath, sources: [...obligationSources],
		decide: registryDecide(pi, decideCommand, judge ?? ompJudge(pi.pi.settings)),
		enforced: name => judgmentReady(judgmentsDir, name),
		askFirst: part => !loadError && new RegExp(map.askFirstCommand).test(part),
		modeOf: (ctx, cwd) => modeOf(ctx, cwd),
		recordAt,
		prStatus: async (ctx, cwd, selector) => prObligation(await prCi(cwd, selector, stateFor(ctx), ctx).catch(error => ({ reason: String(error) }))),
		prOpen: async (ctx, cwd, branch) => {
			const pr = await prCi(cwd, branch, stateFor(ctx), ctx).catch(() => undefined);
			return pr !== undefined && "head" in pr && pr.state === "OPEN";
		},
		repoRun: (argv, cwd, timeout) => repoExec(pi, argv, cwd, timeout),
		checks: { profile: guardrailsProfileCommand, check: guardrailsCommand },
		loadError: () => loadError,
	});
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
		const result = await pi.exec("gh", ["pr", "view", ...(selector ? [selector] : []), "--json", "headRefOid,baseRefName,headRefName,state,statusCheckRollup,url"],
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
		return { ...identity, base: pr.baseRefName, headRef: pr.headRefName, state: pr.state, gate, ciReason: ciReason(pr.statusCheckRollup, gate, identity) };
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
		const deadline = Date.now() + COMMIT_CHECKS_TIMEOUT_MS;
		for (const [label, argv] of runs) {
			const result = await repoExec(pi, argv, root, Math.max(1, deadline - Date.now()));
			if (result.code === 0 && !result.killed) continue;
			return commitFailure(label, root, result, argv === record.gate.commit);
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
		const rules = ompSetting("ttsr.disabledRules");
		const user = session.userRules ??= stringList.parse(rules.get(pi.pi.settings));
		rules.override(pi.pi.settings, at.mode === "structured" ? user : [...new Set([...user, ...map.structuredRules])]);
		return at;
	}
	pi.on("session_start", async (event, ctx) => {
		await rebuild(event, ctx);
		if (loadError) return;
		pinSettings(pi.pi.settings);
		await captureState(trust.snapshot, modesPath, ctx.cwd);
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
	async function commitActionReason(action: GitAction, ctx: ExtensionContext, call: BashCall): Promise<string | undefined> {
		if (!action.cwd) return "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.";
		const root = await repoRoot(action.cwd);
		const at = root ? await modeAt(ctx, root, true) : await modeAt(ctx, action.cwd);
		if (at.why) ctx.ui.notify(`Agent rails: ${at.why}; commit checks do not run.`, "info");
		if (!root || at.mode !== "structured") return undefined;
		return committedTreeReason(action, root, call) ?? (await unstagedReason(pi, action, root)) ?? (await commitChecksReason(root)) ??
			sessionRail.judged.commitReason(root, action, ctx);
	}
	async function mergeActionReason(action: GitAction, state: State, ctx: ExtensionContext): Promise<string | undefined> {
		if (!action.cwd) throw new Error("cannot resolve PR action cwd");
		const merge = prMerge(action.args);
		const head = merge.heads[0];
		const format = adminMergeReason(action.args) ?? prMergeReason(merge) ?? headFormatReason(head);
		if (format || !head) return format;
		const selector = merge.selectors[0];
		const pr = await prCi(action.cwd, selector, state, ctx);
		if (!("head" in pr)) return pr.reason;
		return (await policyMergeReason(pi, action.args, { cwd: action.cwd, head, evidence: trust.evidence }, pr)) ??
			sessionRail.judged.mergeReason(action.cwd, selector, head, ctx);
	}
	async function publishActionReason(action: GitAction, mode: Mode, state: State, ctx: ExtensionContext): Promise<string | undefined> {
		const merge = /^gh pr merge\b/.test(action.command);
		const reason = (await agentMergeReason(action, configRoot)) ?? (merge ? minimalMergeReason(mode) : undefined);
		if (reason) return reason;
		if (/^git push\b/.test(action.command)) return pushReason(pi, action, trust.defaults);
		return merge ? mergeActionReason(action, state, ctx) : undefined;
	}
	async function gitActionReason(step: Step, state: State, ctx: ExtensionContext, call: BashCall): Promise<string | undefined> {
		const { action } = step;
		const bypass = hookBypassReason(action) ?? routeReason(action) ?? await configWriteReason(action);
		if (bypass || !new RegExp(map.gitCommand).test(action.command)) return bypass;
		const mode = action.cwd ? await modeOf(ctx, action.cwd) : "minimal";
		const urls = missing(state, map, mode, ["git"]);
		if (urls.length) return readReason(urls, mode);
		return (await sessionRail.gitWriteReason(action, ctx)) ?? gitWriteKindReason(step, mode, state, ctx, call);
	}
	async function gitWriteKindReason(step: Step, mode: Mode, state: State, ctx: ExtensionContext, call: BashCall): Promise<string | undefined> {
		const { action } = step;
		if (/^git worktree add\b/.test(action.command)) return worktreeReason(pi, ompCommand, action);
		if (/^git commit\b/.test(action.command)) {
			return commitHookReason(action) ?? committedIndexReason(step, mode) ?? await sessionRail.commitReason(action) ?? await commitActionReason(action, ctx, call);
		}
		return PUBLISHES.test(action.command) ? publishActionReason(action, mode, state, ctx) : undefined;
	}
	// at: the alias depth, the bash call, and whether an earlier action in it changed the index.
	async function actionsReason(actions: GitAction[], state: State, ctx: ExtensionContext, at: { depth: number; call: BashCall; staged: boolean }): Promise<string | undefined> {
		const { depth, call } = at;
		if (depth > ALIAS_DEPTH) return `Agent rails: Git and gh aliases nest deeper than ${ALIAS_DEPTH} levels; run the command itself.`;
		let indexChanged = at.staged;
		for (const action of actions) {
			const route = routeReason(action);
			if (route) return route;
			const expanded = await aliasActions(pi, session, action);
			const reason = expanded
				? await actionsReason(expanded, state, ctx, { depth: depth + 1, call, staged: indexChanged })
				: await gitActionReason({ action, staged: indexChanged }, state, ctx, call);
			if (reason) return reason;
			indexChanged ||= INDEX_WRITES.test(action.command) || (expanded ?? []).some(item => INDEX_WRITES.test(item.command));
		}
		return undefined;
	}
	async function bashReason(event: ToolEvent, ctx: ExtensionContext, state: State): Promise<string | undefined> {
		if (event.toolName !== "bash" || event.input.command === undefined) return otherToolReason(event);
		const cwd = absolute(event.input.cwd ?? ctx.cwd, ctx.cwd) ?? ctx.cwd;
		const scan = { commands: [], executables: [], source: event.input.command };
		const actions = await gitActions(event.input.command, cwd, scan);
		const stateReason = shellStateReason(event.input.command, trust.protection) ?? tokenReason(event.input.command);
		if (stateReason || !actions.length) return stateReason;
		return mergeAloneReason(actions) ?? (await trust.snapshot.driftReason()) ?? actionsReason(actions, state, ctx, { depth: 0, call: { actions, executables: scan.executables }, staged: false });
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
			const reason = (await bashReason(event, ctx, state)) ?? (await evalToolReason(trust, event, ctx.cwd)) ??
				protectedEditReason(map, trust.protection, event, ctx.cwd, state) ?? (await subagentReason(map, event, ctx, state)) ??
				(await editReadsReason(event, ctx, state)) ??
				(await sessionRail.judged.scopeReason(event, scopeInputSchema.parse(runtimeEvent.input), ctx));
			return reason ? { block: true, reason } : undefined;
		} catch (error) { return { block: true, reason: `Agent rails: policy could not be evaluated: ${String(error)}` }; }
	});
	pi.on("tool_result", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { additionalContext: loadError };
		try {
			if (event.toolName === "eval") await trust.evidence.end(event.toolCallId, root => commonDir(pi, root));
			return await recordResult(event, ctx, stateFor(ctx), true);
		}
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
		if (at.invalid) {
			return { notice: request.notice, content: resumeContent(request.slug, request.dir, at.why ?? "invalid record") };
		}
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
		try {
			await writeMode(modesPath, slug, mode);
			trust.snapshot.accept(modesPath, await fileState(modesPath));
		} catch (error) { return { notice: notify(`Agent rails: cannot set dynaflows for ${slug}: ${String(error)}`, "error") }; }
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
		state.ownerAsked = /dynaflows/i.test(event.prompt ?? "");
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
		mode: z.enum(MODES).optional().describe("structured when the user asked to enable or start dynaflows, minimal when they asked to disable or stop it"),
		rollout: z.literal(true).optional().describe("true when the user asked to roll this work out to every device"),
		acknowledge: z.literal(true).optional().describe("true once you reviewed a dynaflows extension change the rails asked you to review"),
	});
	pi.registerTool({
		name: "dynaflows",
		label: "Dynaflows",
		description: "Switch dynaflows between minimal mode (fixed guards only) and structured mode (the full workflow) for the " +
			"current repository and get the next step, opt this work into device rollout, or acknowledge a reviewed extension change. " +
			"Pass mode or rollout only when the user asked for it in this conversation, in any wording; never on your own initiative. " +
			"Pass acknowledge only after reviewing an extension change the rails asked you to review.",
		parameters: dynaflowsToolParams,
		loadMode: "essential",
		approval: "write",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			if (loadError) return toolReply(loadError);
			if (ctx.agent.kind !== "main") return toolReply("Agent rails: only the main agent can switch the dynaflows mode.");
			const { mode, rollout, acknowledge } = dynaflowsToolParams.parse(params);
			if (mode === "minimal" && !stateFor(ctx).ownerAsked) {
				return toolReply("Agent rails: switch to minimal mode only when the owner's latest message asks about dynaflows.");
			}
			const replies = [
				mode ? await applyDynaflows(ctx, mode, false).then(result => result.content ?? result.notice) : undefined,
				rollout ? await sessionRail.optInRollout(ctx) : undefined,
				acknowledge ? await sessionRail.acknowledge(ctx) : undefined,
			].filter(reply => reply !== undefined);
			return toolReply(replies.join("\n") || "Agent rails: pass mode, rollout or acknowledge.");
		},
	});
	pi.registerCommand("extension-health-dynaflows", {
		description: "Dynaflows registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Dynaflows registered", loadError ? "error" : "info"); },
	});
}
