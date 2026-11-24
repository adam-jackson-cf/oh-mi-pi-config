import { access, lstat, readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import {
	z, type ExtensionAPI, type ExtensionContext,
	type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent,
} from "@oh-my-pi/pi-coding-agent";
import {
	commonDir, configRoot as defaultConfigRoot, minimalText, OriginError, originUrl, parseOrigin, repoSlug, resolveConfig, unresolvedText,
	type AgentRailsConfig, type Ci, type ConfigState,
} from "./lib/repo-config.ts";
import { defaultModesPath, MODES, type Mode, repoMode, writeMode } from "./lib/modes.ts";
import {
	SUBPROCESS_TIMEOUT_MS, stringList, loadMapping, detailsSchema, policyEvent, absolute, repoRoot,
	paths, pathArguments, matches, missing, sentence, readReason, gitActions, note, fileErrorSchema,
	codeRunsGit, INDEX_WRITES, commitScopeArgument,
	recordRead, subagentPathReason, type GitAction, type Mapping, type State as SharedState, type ToolEvent,
	blockLine, closeEpisodes, coded, insertEntries, openEpisode, RailError, refuse, resolvedLine, storePath as defaultStorePath,
	type Episode, type LedgerLine, type RailCode, type Refusal,
} from "./lib/rails-runtime.ts";
import {
	PUBLISHES, adminMergeReason, autonomyReason, commitHookReason, configWriteReason, hookBypassReason, mergeMethodReason, mergePolicyFor, minimalMergeReason,
	prCreateBranch, prMerge, prMergeReason, pushedBranches, pushReason, routeReason, worktreeReason, type AutonomyApprovals, type DefaultBranches,
} from "./lib/guards.ts";
import {
	Approvals, defaultReviewsPath, ompJudge, registryDecide, scopeInputSchema, sessionRails, type JudgeTransport, type ObligationSource,
	type PrStatus,
} from "./lib/session-rails.ts";
import { registerOwnerSurface } from "./lib/onboarding.ts";
import {
	COMMIT_CHECKS_TIMEOUT_MS, Evidence, Snapshot, evalReason, fileState, pinSettings, protectedPathReason, setting as ompSetting, shellStateReason,
	type Protection,
} from "./lib/trust.ts";

const prCiSchema = z.object({
	number: z.number().int().positive(),
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
// command they run (path check, commands.commit, harness checks) so repos never see the harness.
const harnessVariables = ["AGENT"];
const ALIAS_DEPTH = 8;
// github tool operations that push, open PRs or move local branches.
const GITHUB_TOOL_WRITES = ["pr_create", "pr_push", "pr_checkout"];
// Characters of a failed commit check's output shown to the agent (the tail, where findings end).
const commitOutputLimit = 8000;
// Derived from this file's location so the rails hold no machine path: <omp>/scripts/ci_runner.py.
const ciRunnerPath = resolve(import.meta.dir, "../../../scripts/ci_runner.py");
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
function headFormatReason(head: string | undefined): Refusal | undefined {
	if (!head) return refuse("merge-head-format", "Agent rails: merge requires --match-head-commit <40-hex verified SHA>");
	if (/[$`]/.test(head)) return refuse("unexpanded-shell", `Agent rails: received unexpanded shell text \`${head}\`; rails check the literal command, so pass the 40-hex SHA itself`);
	if (!/^[0-9a-f]{40}$/.test(head)) return refuse("merge-head-format", `Agent rails: --match-head-commit must be a lowercase 40-hex SHA, got ${head}`);
	return undefined;
}
type ExecApi = Pick<ExtensionAPI, "exec">;
type DynaflowsApi = Pick<ExtensionAPI, "on" | "exec" | "registerCommand" | "registerTool" | "sendUserMessage"> & { pi: Pick<ExtensionAPI["pi"], "settings"> };
type Applied = { notice: string; content?: string | undefined };
// The directive's outcome for a repository whose agent-rails config is missing or cannot be resolved.
function unconfiguredOutcome(notify: (notice: string, level?: "info" | "error") => string, slug: string, state: ConfigState, mode: Mode): Applied {
	if (state.kind !== "missing") {
		const reason = "reason" in state ? state.reason : state.kind;
		return { notice: notify(`Agent rails: cannot resolve the agent-rails config of ${slug}: ${reason}; retry.`, "error") };
	}
	if (mode === "minimal") return { notice: notify(`Agent rails: ${slug} has no agent-rails config, so dynaflows is already in minimal mode.`) };
	return { notice: notify(`Agent rails: ${slug} has no agent-rails config at ${state.path}; structured mode starts once the owner onboards it.`), content:
		`The owner asked for dynaflows structured mode in ${slug}, which has no agent-rails config (${state.path}) yet, so it runs minimal mode. ` +
		"Agents never write that file: tell the owner the repository needs onboarding, and apply only the fixed guards until then." };
}
function invalidContent(slug: string, path: string, reason: string): string {
	return `The owner asked for dynaflows structured mode in ${slug}, but its agent-rails config ${path} is refused: ${reason}. ` +
		"Agents never write that file: report the problems to the owner, who repairs it; until then dynaflows runs minimal mode.";
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
	// Tool calls seen this session and the open refusal episodes, for the rails ledger.
	calls: number;
	episodes: Episode[];
	// The owner's one-use autonomy approvals the current tool call relies on; consumed only when the call is allowed.
	autonomyUses: string[];
	// Main checkouts whose onboarding the owner started in this session (/agentrails onboard).
	onboarding: Set<string>;
}
function fresh(): State {
	return {
		reads: new Set(), notes: new Set(), edits: new Set(), pathFailures: new Map(),
		pointed: false, proposal: [], ownerAsked: false, calls: 0, episodes: [], autonomyUses: [], onboarding: new Set(),
	};
}
// A rebuilt session state keeps the pointer, path failures, call count, open ledger episodes and onboarding.
function carried(previous: State | undefined): State {
	const state = fresh();
	if (!previous) return state;
	return {
		...state, pointed: previous.pointed, pathFailures: previous.pathFailures, calls: previous.calls, episodes: previous.episodes,
		onboarding: previous.onboarding,
	};
}
type PathOrigin = { code: "path-check" | "handoff-path-check"; tool: string };
// The subject of a Git action for matching a refusal to a later passing call: `git <verb>` or `gh <group> <verb>`.
function gitVerb(command: string): string {
	return command.split(" ").slice(0, command.startsWith("gh ") ? 3 : 2).join(" ");
}
function toolCwd(event: ToolEvent, ctx: ExtensionContext): string {
	return absolute(event.input.cwd ?? ctx.cwd, ctx.cwd) ?? ctx.cwd;
}
// What a call acts on, to match a refusal with a later passing call: its Git verbs, its edit targets
// or its tool name. Without a mapping (rails.yml failed to load) the subject is the tool name; a
// command the rails cannot parse has no subject, so its refusal never resolves.
async function callSubjects(map: Mapping | undefined, event: ToolEvent, ctx: ExtensionContext, state: State): Promise<string[]> {
	if (!map) return [event.toolName];
	if (event.toolName === "bash" && event.input.command !== undefined) {
		const actions = await gitActions(event.input.command, toolCwd(event, ctx)).catch((): GitAction[] => []);
		return actions.map(action => gitVerb(action.command));
	}
	if (!map.editTools.includes(event.toolName)) return [event.toolName];
	return event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
}
type LedgerScope = { repo: string | null };
type ScopeAt = (cwd: string) => Promise<LedgerScope>;
const HANDOFF = ["session_stop"];
// The rails outcome ledger (lib/rails-ledger.ts): rail_events rows in the decision store for one
// extension instance. A failed write never changes a decision; it is noted for the session.
class RailsLedger {
	readonly file: string;
	readonly scopeAt: ScopeAt;
	constructor(file: string, scopeAt: ScopeAt) {
		this.file = file;
		this.scopeAt = scopeAt;
	}
	// Writes rows with the time, session, and the repository at cwd.
	async write(ctx: ExtensionContext, state: State, cwd: string, lines: LedgerLine[]): Promise<void> {
		if (!lines.length) return;
		try {
			const { repo } = await this.scopeAt(cwd);
			const ts = new Date().toISOString();
			const session = ctx.sessionManager.getSessionId();
			insertEntries(this.file, lines.map(line => ({ ts, session, repo, ...line })));
		} catch (error) {
			note(state, ctx, `Agent rails: cannot write the rails ledger ${this.file}: ${String(error)}`);
		}
	}
	// A tool call's decision: a refusal opens or extends an episode; a pass resolves the episodes it satisfies.
	async call(map: Mapping | undefined, event: ToolEvent, ctx: ExtensionContext, state: State, refusal: Refusal | undefined): Promise<void> {
		if (!refusal && !state.episodes.length) return;
		const subjects = await callSubjects(map, event, ctx, state);
		if (refusal) openEpisode(state.episodes, { code: refusal.code, tool: event.toolName, subjects, call: state.calls });
		const lines = refusal ? [blockLine("block", event.toolName, refusal)] : closeEpisodes(state.episodes, subjects, state.calls).map(resolvedLine);
		await this.write(ctx, state, toolCwd(event, ctx), lines);
	}
	// A path check's outcome for root: a failure opens an episode and becomes a refusal; a pass resolves the root's episodes.
	async path(ctx: ExtensionContext, state: State, root: string, origin: PathOrigin, error: string | undefined): Promise<Refusal | undefined> {
		const subjects = [`path-check:${root}`];
		if (error) {
			openEpisode(state.episodes, { code: origin.code, tool: origin.tool, subjects, call: state.calls });
			return refuse(origin.code, error, `Agent rails: path check failed in ${root}`);
		}
		await this.write(ctx, state, root, closeEpisodes(state.episodes, subjects, state.calls).map(resolvedLine));
		return undefined;
	}
	// A handoff's refusals; a handoff that passes resolves the earlier handoff refusals.
	async handoff(ctx: ExtensionContext, state: State, refusals: Refusal[]): Promise<void> {
		for (const refusal of refusals) {
			if (refusal.code !== "handoff-path-check") openEpisode(state.episodes, { code: refusal.code, tool: "session_stop", subjects: HANDOFF, call: state.calls });
		}
		const lines = refusals.length ? refusals.map(refusal => blockLine("block", "session_stop", refusal)) : closeEpisodes(state.episodes, HANDOFF, state.calls).map(resolvedLine);
		await this.write(ctx, state, ctx.cwd, lines);
	}
}
// One bash call as the rails saw it: every git/gh action and every executable it runs.
type BashCall = { actions: GitAction[]; executables: string[] };
const COMMIT_COMPANIONS = ["cd", "pushd", "popd", "true"];
const READ_ONLY_GIT = /^git (?:status|diff|log|show|rev-parse)\b/;
// The harness checks: guardrails run from the harness's own reference project (never installed
// into the repo), over the whole repo, with the repo's .agentrails/config.json as config. Rails
// append `--repo <worktree>`; the engine finds the main checkout's config itself.
const guardrailsProject = join(import.meta.dir, "engine");
const defaultGuardrailsCommand = ["uv", "run", "--project", guardrailsProject, "guardrails", "check"];
const harnessPathCommand = ["uv", "run", "--project", guardrailsProject, "guardrails", "path-check"];
const defaultConfigCheckCommand = ["uv", "run", "--project", guardrailsProject, "guardrails", "config-check"];
// The judgment registry (engine/workflows.py) as the rails call it: questions, then record.
const defaultDecideCommand = ["uv", "run", "--project", guardrailsProject, "python", join(import.meta.dir, "lib", "decide.py")];
// The harness checks run only after commands.commit passed, so a harness-only finding can mean a repo
// checker already enforces that step and the config has not given the step to it.
const harnessOnlyNote =
	"commands.commit passed. If a repo checker already enforces a failing step's rules, do not reshape repo " +
	"code to the harness copy: report it to the owner, whose .agentrails/config.json can give the step to the repo " +
	"checker (checkers.check.repo).";

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
const untrustedEvidence = (what: string, head: string, path: string) => refuse("merge-evidence-untrusted",
	`Agent rails: the ${what} result for ${head} at ${path} was not written by a ${what} call the rails saw in this omp process; run ${what} for this head here, then merge.`);
async function proveResultReason(pi: ExecApi, cwd: string, head: string, evidence: Evidence): Promise<Refusal | undefined> {
	const path = join(await commonDir(pi, cwd), "omp-prove", `${head}.result.json`);
	const result = z.object({ head: z.literal(head), status: z.literal("pass") });
	const text = await readFile(path, "utf8").catch(() => "");
	if (!parsesAs(result, text)) return refuse("merge-prove", `Agent rails: no prove() pass for ${head} at ${path}; run prove() for this head, then merge.`);
	return evidence.trusts(path, text) ? undefined : untrustedEvidence("prove()", head, path);
}
type StructureVerdict = { status: string; halt: boolean; errors?: string[] | undefined; findings: { item: string; question: string; mode: "enforced" | "shadow" }[] };
// Every enforced finding blocks: only a pass with no enforced finding, no halt and no errors admits the merge.
// A shadow finding is logged for review and never blocks.
function structureBlock(result: StructureVerdict, head: string): Refusal | undefined {
	if (result.status === "incomplete" || result.errors?.length) {
		return refuse("merge-structure", `Agent rails: structure_review() for ${head} is incomplete (${result.errors?.join("; ") || "status incomplete"}); fix the cause and rerun it.`);
	}
	const enforced = result.findings.filter(item => item.mode === "enforced");
	if (result.status === "pass" && !result.halt && !enforced.length) return;
	const findings = enforced.map(item => `${item.item}: ${item.question}`);
	return refuse("merge-structure", `Agent rails: structure findings block ${head}: ${findings.join("; ") || "halt is true"}; fix them and rerun prove() and structure_review() on the new head.`);
}
async function structureResultReason(pi: ExecApi, cwd: string, head: string, evidence: Evidence): Promise<Refusal | undefined> {
	const path = join(await commonDir(pi, cwd), "omp-structure", `${head}.json`);
	const finding = z.object({
		item: z.string(), question: z.string(), mode: z.enum(["enforced", "shadow"]),
		score: z.number().min(0).max(1), threshold: z.number().min(0).max(1),
	}).strict();
	const schema = z.object({
		head: z.literal(head), halt: z.boolean(), status: z.enum(["pass", "incomplete", "blocked"]),
		findings: z.array(finding), parent: z.string().nullable().optional(),
		pairs: z.record(z.string(), z.unknown()).optional(), kind_files: z.record(z.string(), z.unknown()).optional(),
		consumers: z.record(z.string(), z.unknown()).optional(), errors: z.array(z.string()).optional(),
		thresholds: z.unknown().optional(), version_key: z.string().nullable().optional(),
		model_reported: z.string().nullable().optional(), model_note: z.string().optional(),
		prComment: z.string().optional(),
	}).strict();
	let result;
	let text = "";
	try { text = await readFile(path, "utf8"); result = schema.parse(JSON.parse(text)); }
	catch { return refuse("merge-structure", `Agent rails: no valid structure_review() result for ${head} at ${path}; run structure_review() for this head, then merge.`); }
	if (!evidence.trusts(path, text)) return untrustedEvidence("structure_review()", head, path);
	return structureBlock(result, head);
}
type PrCi = { reason: Refusal } | {
	number: number; head: string; repository: string; base: string; headRef: string; state: string; config: AgentRailsConfig | undefined; ciReason: Refusal | undefined;
};
// The turn-end status of the agent's PR: open is unmet while agents may merge; pending or failing CI
// and an unreadable PR are unmet with their reason, so the turn waits for CI instead of settling.
// A repository whose autonomy.merge is ask leaves the merge to the owner.
function prObligation(pr: PrCi): PrStatus {
	if (!("head" in pr)) return { status: "unmet", reason: pr.reason.reason };
	if (pr.state !== "OPEN" || pr.config?.autonomy.merge === "ask") return { status: "met" };
	return { status: "unmet", reason: pr.ciReason?.reason };
}
type PrIdentity = { head: string; repository: string };
type PrState = Exclude<PrCi, { reason: Refusal }>;
type MergeAt = { cwd: string; head: string; evidence: Evidence };
type Proofs = { prove: boolean; structure: boolean };
// The proof a merge needs: none unless its policy asks for proof; then gates.prove and gates.structureReview
// decide whether prove() and structure_review() results are required.
function proofsFor(proof: boolean, gates: AgentRailsConfig["gates"] | undefined): Proofs {
	return { prove: proof && gates?.prove !== false, structure: proof && gates?.structureReview !== false };
}
async function proofReason(pi: ExecApi, at: MergeAt, proofs: Proofs): Promise<Refusal | undefined> {
	const prove = proofs.prove ? await proveResultReason(pi, at.cwd, at.head, at.evidence) : undefined;
	if (prove || !proofs.structure) return prove;
	return structureResultReason(pi, at.cwd, at.head, at.evidence);
}
// The merge policy for the PR's base and head decides the method and whether proof is needed.
async function policyMergeReason(pi: ExecApi, args: string[], at: MergeAt, pr: PrState): Promise<Refusal | undefined> {
	const policy = mergePolicyFor(pr.config?.mergePolicies ?? [], pr.base, pr.headRef);
	const method = mergeMethodReason(args, policy);
	if (method) return method;
	const proofs = proofsFor(policy.proof, pr.config?.gates);
	if (pr.head !== at.head) return refuse("merge-head-stale", `Agent rails: PR head is ${pr.head}, not ${at.head}; ${proofs.prove ? "prove() the current head" : "merge the current head"}`);
	return (await proofReason(pi, at, proofs)) ?? pr.ciReason;
}
async function foreignRepository(pi: ExecApi, root: string, selector: string | undefined, repository: string): Promise<boolean> {
	if (!selector || !/^https?:[/]{2}/i.test(selector)) return false;
	return (await primaryGithubRepository(pi, root)).toLowerCase() !== repository.toLowerCase();
}
function requiredCiReason(checks: CheckReport[], required: { requiredContext: string; runner: string }, pr: PrIdentity): Refusal | undefined {
	const { requiredContext, runner } = required;
	const named = checks.filter(check => check.name === requiredContext);
	if (named.length && named.every(check => check.green)) return undefined;
	const command = (verb: string) => `python3 ${shellWord(ciRunnerPath)} ${verb} ${shellWord(runner)} ${pr.repository} ${pr.head}`;
	return refuse("merge-ci", `Agent rails: required CI context "${requiredContext}" is ${named.find(check => !check.green)?.state ?? "missing"} for ${pr.head} in ${pr.repository} (runner ${runner}). ` +
		`Submit and wait: ${command("submit")}, then ${command("wait")} --context ${shellWord(requiredContext)}; ` +
		"see skill://git-tool/references/merge-and-recover.md for failures.");
}
// The config's ci decides the merge's CI requirement: the exact-head required context when enabled, none when disabled.
function ciReason(checks: CheckReport[], ci: Ci | undefined, pr: PrIdentity): Refusal | undefined {
	if (ci === undefined) return refuse("merge-ci", `Agent rails: ${pr.repository} has no valid agent-rails config, so its CI requirement for ${pr.head} is unknown; the owner merges.`);
	return ci.enabled ? requiredCiReason(checks, ci, pr) : undefined;
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
type Origin = { slug: string | null; reason?: string };
/** The clone's GitHub slug, or why it has none; an origin remote that cannot be read throws OriginError. */
async function originAt(root: string): Promise<Origin> {
	const url = await originUrl(root);
	if (url === null) return { slug: null, reason: "no origin remote" };
	const slug = parseOrigin(url);
	return slug === null ? { slug, reason: `origin ${url.trim()} is not a GitHub owner/name URL` } : { slug };
}
/** Why a config state is not one the rails can run: the engine's problems, or why it could not be resolved. */
function configFault(state: Exclude<ConfigState, { kind: "ok" | "missing" }>): string {
	return state.kind === "invalid"
		? `the agent-rails config ${state.path} is invalid: ${state.problems.join("; ")}`
		: `cannot resolve this repository's agent-rails config: ${state.reason}`;
}
// The mode file names the mode; structured mode also needs a config the engine accepts, else the repo runs minimal.
async function resolvedMode(modesPath: string, slug: string, config: () => Promise<ConfigState>, strict: boolean): Promise<ModeAt> {
	try {
		if (await repoMode(modesPath, slug) === "minimal") return { mode: "minimal", why: minimalText(slug, `the mode file ${modesPath} does not set structured`) };
		const state = await config();
		if (state.kind === "ok") return { mode: "structured" };
		if (state.kind === "missing") return { mode: "minimal", why: minimalText(slug, `no agent-rails config at ${state.path}`) };
		throw state.kind === "invalid" ? new Error(configFault(state)) : new RailError("config-unresolved", configFault(state));
	} catch (error) {
		if (strict) throw error;
		return { mode: "minimal", invalid: true, why: minimalText(slug, error instanceof Error ? error.message : String(error)) };
	}
}
// The agent-rails config of the repository holding cwd, re-read (and engine-checked once per content) on every action.
function configAt(pi: ExecApi, cwd: string): Promise<ConfigState> {
	return resolveConfig(pi, guardrailsProject, cwd);
}
// The config publishing and merges run under: none for an unconfigured repository; an invalid or
// unresolvable config refuses the action.
async function publishConfig(pi: ExecApi, cwd: string): Promise<AgentRailsConfig | undefined> {
	const root = await repoRoot(cwd);
	if (!root) throw new Error("publishing requires a validated local Git repository");
	const state = await configAt(pi, root);
	if (state.kind === "ok") return state.config;
	if (state.kind === "missing" || state.kind === "outside") return undefined;
	if (state.kind === "invalid") throw new Error(configFault(state));
	throw new RailError("config-unresolved", configFault(state));
}
// The config of a repository whose rails run, when the engine accepted it; undefined for any other state.
async function acceptedConfig(pi: ExecApi, root: string): Promise<AgentRailsConfig | undefined> {
	const state = await configAt(pi, root);
	return state.kind === "ok" ? state.config : undefined;
}
// The repo config's autonomy.branch for each branch a git push sends, once pushReason passed it.
async function branchAutonomyReason(pi: ExecApi, action: GitAction, cwd: string, approved: AutonomyApprovals): Promise<Refusal | undefined> {
	const config = await publishConfig(pi, cwd);
	if (config?.autonomy.branch !== "ask") return undefined;
	for (const branch of await pushedBranches(pi, cwd, action.args)) {
		const reason = autonomyReason({ action: "branch", subject: branch }, config, approved);
		if (reason) return reason;
	}
	return undefined;
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
	if (!expansion) throw new RailError("git-external-command", `git ${name} is neither a Git command nor an alias; external git-* programs run outside the rails`);
	const words = rest.map(shellWord).join(" ");
	if (expansion.startsWith("!")) return gitActions(`${expansion.slice(1)} ${words}`, action.cwd);
	const config = action.config.map(entry => `-c ${shellWord(entry)}`).join(" ");
	return gitActions(`git ${config} ${expansion} ${words}`, action.cwd);
}
async function subagentReason(map: Mapping, event: ToolEvent, ctx: ExtensionContext, state: State): Promise<Refusal | undefined> {
	if (!map.editTools.includes(event.toolName) || ctx.agent.kind === "main") return undefined;
	return subagentPathReason([...pathArguments(event, map), ...(event.input.path === map.resolveDevice ? state.proposal : [])]);
}
type CommitRun = { code: RailCode; label: string; argv: string[] };
function commitFailure(run: CommitRun, root: string, result: { code: number; killed?: boolean; stdout: string; stderr: string }, repoOwned: boolean): Refusal {
	if (result.killed) return refuse(run.code, `Agent rails: ${run.label} did not finish within the commit rail's ${COMMIT_CHECKS_TIMEOUT_MS / 1000}s budget in ${root}; make commands.commit faster, then commit again.`);
	const output = `${result.stdout}\n${result.stderr}`.trim();
	const failed = `Agent rails: ${run.label} failed in ${root} (exit ${result.code})`;
	return refuse(run.code, `${failed}; ` +
		`fix every finding in the repo, then commit again:\n${output.length > commitOutputLimit ? `…${output.slice(-commitOutputLimit)}` : output}${repoOwned ? "" : `\n${harnessOnlyNote}`}`, failed);
}
// A GitHub token in a command lets any program call the API outside the rails.
function tokenReason(command: string): Refusal | undefined {
	return /\$\{?(?:GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN)\b/.test(command)
		? refuse("github-token", "Agent rails: commands may not read the GitHub token; use gh and git directly so the rails check them.")
		: undefined;
}
type Trust = { evidence: Evidence; snapshot: Snapshot; defaults: DefaultBranches; protection: Protection };
// The checks run before the command, so a commit must be the bash call's only change.
function committedTreeReason(action: GitAction, root: string, call: BashCall): Refusal | undefined {
	const others = call.actions.filter(other => other !== action && !READ_ONLY_GIT.test(other.command));
	const commands = call.executables.filter(command => command !== "git" && !COMMIT_COMPANIONS.includes(command));
	return others.length || commands.length
		? refuse("commit-alone", `Agent rails: run git commit in ${root} as its own bash command (stage in an earlier call) so the commit checks see the tree being committed.`)
		: undefined;
}
// commands.commit runs on the working tree, so it must match what git commit records.
async function unstagedReason(pi: ExecApi, action: GitAction, root: string): Promise<Refusal | undefined> {
	if (action.args.slice(1).some(arg => arg === "--all" || /^-[^-]*a/.test(arg))) return undefined;
	const diff = await pi.exec("git", ["diff", "--name-only"], { cwd: root, timeout: SUBPROCESS_TIMEOUT_MS });
	if (diff.code !== 0 || diff.killed) throw new Error(`git diff failed: ${diff.stderr}`);
	return diff.stdout.trim()
		? refuse("commit-unstaged", `Agent rails: unstaged changes in ${root} would reach the commit checks but not the commit; stage or stash them first:\n${diff.stdout.trim()}`,
			`Agent rails: unstaged changes in ${root} would reach the commit checks but not the commit; stage or stash them first.`)
		: undefined;
}
async function evalToolReason(trust: Trust, event: ToolEvent, cwd: string): Promise<Refusal | undefined> {
	if (event.toolName !== "eval") return undefined;
	const code = event.input.code ?? "";
	const reason = coded("eval-refused", evalReason(code, trust.protection));
	if (!reason) await trust.evidence.begin(event.toolCallId, code, cwd);
	return reason;
}
function protectedEditReason(map: Mapping, protection: Protection, event: ToolEvent, cwd: string, state: State): Refusal | undefined {
	if (!map.editTools.includes(event.toolName)) return undefined;
	const targets = [...paths(event, map, cwd), ...(event.input.path === map.resolveDevice ? state.proposal : [])];
	for (const target of targets) {
		const reason = coded("protected-path", protectedPathReason(target, protection));
		if (reason) return reason;
	}
	return undefined;
}
// The rail state a session trusts: the mode file and the session repo's origin. The repo's
// agent-rails config is not snapshotted: agents cannot write it, and the rails re-read it on every action.
async function captureState(snapshot: Snapshot, modesFile: string, cwd: string, notify: (text: string, level: "error") => void): Promise<void> {
	await snapshot.capture(modesFile, () => fileState(modesFile));
	const root = await repoRoot(cwd);
	if (!root) return;
	const key = `the origin remote of ${root}`;
	const read = async () => `${await repoSlug(root)}`;
	try { await snapshot.capture(key, read); }
	catch (error) {
		if (!(error instanceof OriginError)) throw error;
		// No trusted origin: later git and gh actions are refused until the owner restarts omp.
		snapshot.unread(key, read, error.message);
		notify(`Agent rails: could not read ${key} at session start (${error.message}); git and gh actions are refused until the owner restarts omp.`, "error");
	}
}
async function driftOutcome(snapshot: Snapshot): Promise<{ reason?: Refusal | undefined; unresolved?: OriginError | undefined }> {
	try { return { reason: coded("rail-state-drift", await snapshot.driftReason()) }; }
	catch (error) {
		if (error instanceof OriginError) return { unresolved: error };
		throw error;
	}
}
function notedUnresolved(reason: Refusal | undefined, unresolved: OriginError | undefined, state: State, ctx: ExtensionContext): Refusal | undefined {
	if (!reason && unresolved) note(state, ctx, unresolvedText(unresolved));
	return reason;
}
function mergeAloneReason(actions: GitAction[]): Refusal | undefined {
	return actions.length > 1 && actions.some(action => /^gh pr merge\b/.test(action.command))
		? refuse("merge-alone", "Agent rails: run gh pr merge as its own bash command, so the head the rails check is the head gh merges.")
		: undefined;
}
type Notify = (notice: string, level?: "info" | "error") => string;
// unresolved: the origin remote could not be read for this bash call, so every git or gh write in it is refused.
type Step = { action: GitAction; staged: boolean; unresolved?: OriginError | undefined };
// The staged checks (handoff, commit checks, implementation-integrity) read the index before the command runs,
// so the committed tree must be that index.
function committedIndexReason(step: Step, mode: Mode): Refusal | undefined {
	if (step.staged) return refuse("commit-after-staging", "Agent rails: run git commit in a command of its own after staging; the rails check the index as it is before the command runs.");
	const scope = mode === "structured" ? commitScopeArgument(step.action.args) : undefined;
	return scope === undefined ? undefined
		: refuse("commit-scope", `Agent rails: structured commits commit the staged index only; stage with git add, then run git commit without -a, -i, -o, -p or paths (got ${scope}).`);
}
// Git writes through tools other than bash cannot be checked: eval code that starts git or gh, github tool writes.
function otherToolReason(event: ToolEvent): Refusal | undefined {
	if (event.toolName === "eval" && event.input.code !== undefined && codeRunsGit(event.input.code)) {
		return refuse("eval-git", "Agent rails: eval code that runs git or gh cannot be checked; run Git and GitHub commands through bash.");
	}
	if (event.toolName === "github" && GITHUB_TOOL_WRITES.includes(event.input.op ?? "")) {
		return refuse("github-tool-write", `Agent rails: the github tool's ${event.input.op} writes outside the Git rails; run git or gh through bash.`);
	}
	return undefined;
}
function workflowReferences(map: Mapping): string {
	return (map.references.structured["workflow"] ?? []).join(" and ");
}
type ScopeInput = Parameters<ReturnType<typeof sessionRails>["judged"]["scopeReason"]>[1];
type StructuredRequest = { notice: string; slug: string; path: string; task: boolean };

type DynaflowsOptions = {
	modesPath: string; configPath: string; configRoot: string; guardrailsCommand: string[]; guardrailsConfigCheckCommand: string[];
	ompCommand: string[]; extensionDir: string; reviewsPath: string; obligationSources: ObligationSource[];
	/** The decision store the rails ledger writes (lib/rails-ledger.ts); never a repository. */
	storePath: string;
	decideCommand: string[];
	/** The judge transport for judged rails; omp's judge role unless a test stubs it. */
	judge: ((ctx: ExtensionContext) => JudgeTransport) | undefined;
};
function defaultOptions(): DynaflowsOptions {
	return {
		modesPath: defaultModesPath(),
		configPath: join(import.meta.dir, "rails.yml"),
		configRoot: defaultConfigRoot(),
		guardrailsCommand: defaultGuardrailsCommand,
		guardrailsConfigCheckCommand: defaultConfigCheckCommand,
		ompCommand: ["omp"],
		extensionDir: import.meta.dir,
		// The omp agent dir, by the same rule as the judgment call log; never a repository.
		reviewsPath: defaultReviewsPath(),
		storePath: defaultStorePath(),
		obligationSources: [],
		decideCommand: defaultDecideCommand,
		judge: undefined,
	};
}

export default function dynaflows(pi: DynaflowsApi, options: Partial<DynaflowsOptions> = {}): void {
	const {
		modesPath, configPath, configRoot, guardrailsCommand, guardrailsConfigCheckCommand, ompCommand, extensionDir, reviewsPath, obligationSources,
		decideCommand, judge, storePath,
	}: DynaflowsOptions = { ...defaultOptions(), ...options };
	// A ledger row's repository at cwd; one that cannot be resolved (no origin) is recorded as null.
	const ledger = new RailsLedger(storePath, async cwd => {
		const root = await repoRoot(cwd).catch(() => undefined);
		return { repo: root ? await repoSlug(root).catch(() => null) : null };
	}), states = new Map<string, State>(), session: SessionCache = {}, approvals = new Approvals(), trust: Trust = {
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
	const stateFor = (ctx: Pick<ExtensionContext, "sessionManager">) => {
		const id = ctx.sessionManager.getSessionId();
		let state = states.get(id);
		if (!state) {
			state = fresh();
			states.set(id, state);
		}
		return state;
	};
	const autonomyApprovals = (ctx: ExtensionContext, state: State): AutonomyApprovals => ({
		has: key => approvals.has(ctx.sessionManager.getSessionId(), key),
		use: key => { state.autonomyUses.push(key); },
	});
	const sessionRail = sessionRails(pi, {
		configRoot, extensionDir, reviewsPath, sources: [...obligationSources],
		commonDir: cwd => commonDir(pi, cwd),
		decide: registryDecide(pi, decideCommand, judge ?? ompJudge(pi.pi.settings)),
		approvals,
		askFirst: part => !loadError && new RegExp(map.askFirstCommand).test(part),
		modeOf: (ctx, cwd) => modeOf(ctx, cwd),
		configOf: root => acceptedConfig(pi, root),
		prStatus: (_ctx, cwd, selector) => prCi(cwd, selector).then(prObligation, (error: Error): PrStatus => ({ status: "unmet", reason: String(error) })),
		prOpen: async (_ctx, cwd, branch) => {
			const pr = await prCi(cwd, branch).catch(() => undefined);
			return pr !== undefined && "head" in pr && pr.state === "OPEN";
		},
		repoRun: (argv, cwd, timeout) => repoExec(pi, argv, cwd, timeout),
		checks: { config: guardrailsConfigCheckCommand, check: guardrailsCommand },
		loadError: () => loadError,
	});
	// The owner's surface for the repo config: /agentrails, the agentrails_config tool and the $approve/$revoke lines.
	registerOwnerSurface(pi, {
		check: { argv: defaultConfigCheckCommand }, engineDir: guardrailsProject, skillsDir: join(import.meta.dir, "skills"), session: stateFor,
	});
	async function prCi(cwd: string | undefined, selector: string | undefined): Promise<PrCi> {
		if (!cwd) throw new Error("cannot resolve PR action cwd");
		const root = await repoRoot(cwd);
		if (!root) throw new Error("PR checks require a validated local Git repository");
		const result = await pi.exec("gh", ["pr", "view", ...(selector ? [selector] : []), "--json", "number,headRefOid,baseRefName,headRefName,state,statusCheckRollup,url"],
			{ cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code !== 0 || result.killed) return {
			reason: refuse("merge-pr-unreadable", `Agent rails: cannot read PR head/CI via gh: ${result.stderr.trim()}; authenticate the GitHub account that owns this repository (gh auth switch) and retry.`,
				"Agent rails: cannot read PR head/CI via gh; authenticate the GitHub account that owns this repository (gh auth switch) and retry."),
		};
		const pr = prCiSchema.parse(JSON.parse(result.stdout));
		const identity = { head: pr.headRefOid, repository: new URL(pr.url).pathname.split("/").slice(1, 3).join("/") };
		if (await foreignRepository(pi, root, selector, identity.repository)) {
			return { reason: refuse("merge-foreign-repo", `Agent rails: run from a checkout of ${identity.repository}`) };
		}
		const config = await publishConfig(pi, root);
		return {
			...identity, number: pr.number, base: pr.baseRefName, headRef: pr.headRefName, state: pr.state, config,
			ciReason: ciReason(pr.statusCheckRollup, config?.ci, identity),
		};
	}
	async function modeAt(ctx: ExtensionContext, cwd = ctx.cwd, strict = false): Promise<ModeAt> {
		const root = await repoRoot(cwd);
		const origin: Origin = root ? await originAt(root) : { slug: null, reason: "not in a Git repository" };
		if (!root || !origin.slug) return { mode: "minimal", why: minimalText(null, origin.reason ?? "no GitHub origin") };
		return resolvedMode(modesPath, origin.slug, () => configAt(pi, root), strict);
	}
	async function modeOf(ctx: ExtensionContext, cwd = ctx.cwd): Promise<Mode> {
		const at = await modeAt(ctx, cwd);
		if (at.invalid && at.why) note(stateFor(ctx), ctx, sentence(`Agent rails: ${at.why}`));
		return at.mode;
	}
	// For actions that write nothing through git or gh: an unresolvable mode is notified, the action
	// goes on, and no structured-only addition runs. Git and gh writes call modeOf and refuse instead.
	async function structuredAt(ctx: ExtensionContext, cwd = ctx.cwd): Promise<boolean> {
		try { return await modeOf(ctx, cwd) === "structured"; }
		catch (error) {
			if (!(error instanceof OriginError)) throw error;
			note(stateFor(ctx), ctx, unresolvedText(error));
			return false;
		}
	}
	// The dynaflows directive's target: the repo's slug and its agent-rails config state. Throws
	// outside a Git project with a GitHub origin remote.
	async function directiveTarget(ctx: ExtensionContext): Promise<{ slug: string; config: ConfigState }> {
		const root = await repoRoot(ctx.cwd);
		if (!root) throw new Error("repo toggle needs a Git project");
		const origin = await originAt(root);
		if (!origin.slug) throw new Error(`repo toggle needs a Git project with a GitHub origin remote: ${root} has ${origin.reason}`);
		return { slug: origin.slug, config: await configAt(pi, root) };
	}
	async function runPathCheck(canonicalRoot: string, changed: string[]): Promise<void> {
		const result = await repoExec(pi, [...harnessPathCommand, "--repo", canonicalRoot, "--event", "edit", ...changed], canonicalRoot, SUBPROCESS_TIMEOUT_MS);
		if (result.code !== 0 || result.killed) throw new Error(`${result.stdout}\n${result.stderr}`.trim());
	}
	async function checkPaths(root: string, changed: string[], state: State): Promise<string | undefined> {
		let pending = changed;
		try {
			const canonicalRoot = await realpath(root);
			pending = await canonicalCandidates(root, canonicalRoot, pending);
			const added = await statusAdditions(pi, canonicalRoot);
			pending = pending.filter(path => added.has(relative(canonicalRoot, path)));
			if (pending.length) await runPathCheck(canonicalRoot, pending);
			state.pathFailures.delete(root);
			return undefined;
		} catch (error) {
			state.pathFailures.set(root, pending);
			return `Agent rails: path check failed in ${root}: ${String(error)}`;
		}
	}
	// gates.pathCheck runs the staged path check; gates.commitChecks runs commands.commit, then the harness checks.
	async function commitChecksReason(root: string): Promise<Refusal | undefined> {
		const config = await acceptedConfig(pi, root);
		if (!config) return undefined;
		const { gates, commands } = config;
		if (gates.pathCheck) {
			const pathResult = await repoExec(pi, [...harnessPathCommand, "--repo", root, "--event", "staged"], root, SUBPROCESS_TIMEOUT_MS);
			if (pathResult.code !== 0 || pathResult.killed) {
				return refuse("staged-path-check", `Agent rails: staged path check failed in ${root}: ${pathResult.stdout}\n${pathResult.stderr}\nFix the findings, then commit again.`,
					`Agent rails: staged path check failed in ${root}`);
			}
		}
		if (!gates.commitChecks) return undefined;
		const runs: CommitRun[] = [
			{ code: "commit-checks", label: `commands.commit (${commands.commit.join(" ")})`, argv: commands.commit },
			{ code: "harness-checks", label: "harness checks (guardrails)", argv: [...guardrailsCommand, "--repo", root] },
		];
		const deadline = Date.now() + COMMIT_CHECKS_TIMEOUT_MS;
		for (const run of runs) {
			const result = await repoExec(pi, run.argv, root, Math.max(1, deadline - Date.now()));
			if (result.code === 0 && !result.killed) continue;
			return commitFailure(run, root, result, run.argv === commands.commit);
		}
		return undefined;
	}
	async function checkGroups(groups: Map<string, string[]>, ctx: ExtensionContext, state: State, tool: string): Promise<Refusal[]> {
		const errors: Refusal[] = [];
		for (const [root, changedPaths] of groups) {
			if (!await structuredAt(ctx, root)) continue;
			if (!(await acceptedConfig(pi, root))?.gates.pathCheck) {
				state.pathFailures.delete(root);
				continue;
			}
			const pending = [...new Set([...(state.pathFailures.get(root) ?? []), ...changedPaths])];
			const error = await ledger.path(ctx, state, root, { code: "path-check", tool }, await checkPaths(root, pending, state));
			if (!error) continue;
			errors.push(error);
			await ledger.write(ctx, state, root, [blockLine("context", tool, error)]);
		}
		return errors;
	}
	async function recordResult(event: ToolEvent, ctx: ExtensionContext, state: State, runCheck: boolean) {
		recordRead(event, state);
		if (event.isError || proposalOnly(map, event, ctx, state) || !map.editTools.includes(event.toolName)) return undefined;
		const groups = await groupByRoot(map, editedPaths(map, event, ctx, state), state);
		const errors = runCheck ? await checkGroups(groups, ctx, state, event.toolName) : [];
		return errors.length ? { additionalContext: errors.map(error => error.reason).join("\n") } : undefined;
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
		const state = carried(states.get(ctx.sessionManager.getSessionId()));
		states.set(ctx.sessionManager.getSessionId(), state);
		if (loadError) { note(state, ctx, loadError); return; }
		const calls = new Map<string, ToolEvent>();
		for (const entry of ctx.sessionManager.getBranch()) await replayEntry(entry, calls, ctx, state);
	}
	// An unresolvable mode is notified and leaves the structured rules on: never minimal on an error.
	async function syncRules(ctx: ExtensionContext): Promise<ModeAt | undefined> {
		const at = await modeAt(ctx).catch((error: Error) => {
			ctx.ui.notify(error instanceof OriginError ? unresolvedText(error) : `Agent rails: could not resolve this repository's dynaflows mode: ${String(error)}; retry.`, "error");
			return undefined;
		});
		const rules = ompSetting("ttsr.disabledRules");
		const user = session.userRules ??= stringList.parse(rules.get(pi.pi.settings));
		rules.override(pi.pi.settings, at?.mode === "minimal" ? [...new Set([...user, ...map.structuredRules])] : user);
		return at;
	}
	pi.on("session_start", async (event, ctx) => {
		await rebuild(event, ctx);
		if (loadError) return;
		pinSettings(pi.pi.settings);
		await captureState(trust.snapshot, modesPath, ctx.cwd, (text, level) => ctx.ui.notify(text, level));
		const at = await syncRules(ctx);
		if (at?.mode === "minimal" && at.why) note(stateFor(ctx), ctx, sentence(`Agent rails: ${at.why}`));
	});
	pi.on("session_switch", rebuild);
	pi.on("session_branch", rebuild);
	pi.on("session_tree", rebuild);
	pi.on("session_compact", (_event, ctx) => {
		const state = stateFor(ctx);
		state.reads.clear();
	});
	async function commitActionReason(action: GitAction, ctx: ExtensionContext, call: BashCall): Promise<Refusal | undefined> {
		if (!action.cwd) return refuse("cwd-unresolved", "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.");
		const root = await repoRoot(action.cwd);
		const at = root ? await modeAt(ctx, root, true) : await modeAt(ctx, action.cwd);
		if (at.why) ctx.ui.notify(`Agent rails: ${at.why}; commit checks do not run.`, "info");
		if (!root || at.mode !== "structured") return undefined;
		return committedTreeReason(action, root, call) ?? (await unstagedReason(pi, action, root)) ?? (await commitChecksReason(root)) ??
			coded("judged-integrity", await sessionRail.judged.commitReason(root, action, ctx));
	}
	async function mergeActionReason(action: GitAction, state: State, ctx: ExtensionContext): Promise<Refusal | undefined> {
		if (!action.cwd) throw new Error("cannot resolve PR action cwd");
		const merge = prMerge(action.args);
		const head = merge.heads[0];
		const format = adminMergeReason(action.args) ?? prMergeReason(merge) ?? headFormatReason(head);
		if (format || !head) return format;
		const selector = merge.selectors[0];
		const pr = await prCi(action.cwd, selector);
		if (!("head" in pr)) return pr.reason;
		const autonomy = autonomyReason({ action: "merge", subject: String(pr.number) }, pr.config, autonomyApprovals(ctx, state));
		if (autonomy) return autonomy;
		return (await policyMergeReason(pi, action.args, { cwd: action.cwd, head, evidence: trust.evidence }, pr)) ??
			coded("judged-evidence", await sessionRail.judged.mergeReason(action.cwd, selector, head, ctx));
	}
	// Publishing: git push (default-branch block, then autonomy.branch), gh pr create (autonomy.pr) and
	// gh pr merge (minimal-mode block, then the merge checks with autonomy.merge).
	async function publishActionReason(action: GitAction, mode: Mode, state: State, ctx: ExtensionContext): Promise<Refusal | undefined> {
		const { cwd } = action;
		if (!cwd) return refuse("cwd-unresolved", "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push or gh pr create/merge.");
		const approved = autonomyApprovals(ctx, state);
		if (/^git push\b/.test(action.command)) return (await pushReason(pi, action, trust.defaults)) ?? branchAutonomyReason(pi, action, cwd, approved);
		if (/^gh pr create\b/.test(action.command)) return autonomyReason({ action: "pr", subject: await prCreateBranch(pi, action) }, await publishConfig(pi, cwd), approved);
		return minimalMergeReason(mode) ?? mergeActionReason(action, state, ctx);
	}
	async function gitActionReason(step: Step, state: State, ctx: ExtensionContext, call: BashCall): Promise<Refusal | undefined> {
		const { action } = step;
		const bypass = hookBypassReason(action) ?? routeReason(action) ?? await configWriteReason(action);
		if (bypass || !new RegExp(map.gitCommand).test(action.command)) return bypass;
		if (step.unresolved) return refuse("mode-unresolved", unresolvedText(step.unresolved));
		const mode = action.cwd ? await modeOf(ctx, action.cwd) : "minimal";
		const urls = missing(state, map, mode, ["git"]);
		if (urls.length) return readReason(urls, mode);
		return coded("extension-review", await sessionRail.gitWriteReason(action, ctx)) ?? gitWriteKindReason(step, mode, state, ctx, call);
	}
	async function gitWriteKindReason(step: Step, mode: Mode, state: State, ctx: ExtensionContext, call: BashCall): Promise<Refusal | undefined> {
		const { action } = step;
		if (/^git worktree add\b/.test(action.command)) return worktreeReason(pi, ompCommand, action);
		if (/^git commit\b/.test(action.command)) {
			return commitHookReason(action) ?? committedIndexReason(step, mode) ?? coded("handoff-staged", await sessionRail.commitReason(action)) ?? await commitActionReason(action, ctx, call);
		}
		return PUBLISHES.test(action.command) ? publishActionReason(action, mode, state, ctx) : undefined;
	}
	// at: the alias depth, the bash call, and whether an earlier action in it changed the index.
	async function actionsReason(actions: GitAction[], state: State, ctx: ExtensionContext, at: { depth: number; call: BashCall; staged: boolean; unresolved?: OriginError | undefined }): Promise<Refusal | undefined> {
		const { depth, call, unresolved } = at;
		if (depth > ALIAS_DEPTH) return refuse("alias-depth", `Agent rails: Git and gh aliases nest deeper than ${ALIAS_DEPTH} levels; run the command itself.`);
		let indexChanged = at.staged;
		for (const action of actions) {
			const route = routeReason(action);
			if (route) return route;
			const expanded = await aliasActions(pi, session, action);
			const reason = expanded
				? await actionsReason(expanded, state, ctx, { depth: depth + 1, call, staged: indexChanged, unresolved })
				: await gitActionReason({ action, staged: indexChanged, unresolved }, state, ctx, call);
			if (reason) return reason;
			indexChanged ||= INDEX_WRITES.test(action.command) || (expanded ?? []).some(item => INDEX_WRITES.test(item.command));
		}
		return undefined;
	}
	async function bashReason(event: ToolEvent, ctx: ExtensionContext, state: State): Promise<Refusal | undefined> {
		if (event.toolName !== "bash" || event.input.command === undefined) return otherToolReason(event);
		const scan = { commands: [], executables: [], source: event.input.command };
		const actions = await gitActions(event.input.command, toolCwd(event, ctx), scan);
		const stateReason = coded("rail-state-write", shellStateReason(event.input.command, trust.protection)) ?? tokenReason(event.input.command);
		if (stateReason || !actions.length) return stateReason;
		// An origin remote that cannot be read now refuses every git or gh write in the call; the rest go on, notified.
		const drift = await driftOutcome(trust.snapshot);
		return mergeAloneReason(actions) ?? drift.reason ?? notedUnresolved(
			await actionsReason(actions, state, ctx, { depth: 0, call: { actions, executables: scan.executables }, staged: false, unresolved: drift.unresolved }),
			drift.unresolved, state, ctx);
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
	async function editReadsReason(event: ToolEvent, ctx: ExtensionContext, state: State): Promise<Refusal | undefined> {
		if (!map.editTools.includes(event.toolName)) return undefined;
		const targets = event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
		const required = new Set<string>();
		for (const path of targets) {
			for (const url of missing(state, map, "structured", await pathTriggers(path, ctx, state))) required.add(url);
		}
		return required.size ? readReason([...required], "structured") : undefined;
	}
	async function callReason(event: ToolEvent, scope: ScopeInput, ctx: ExtensionContext, state: State): Promise<Refusal | undefined> {
		return (await bashReason(event, ctx, state)) ?? (await evalToolReason(trust, event, ctx.cwd)) ??
			protectedEditReason(map, trust.protection, event, ctx.cwd, state) ?? (await subagentReason(map, event, ctx, state)) ??
			(await editReadsReason(event, ctx, state)) ?? coded("judged-scope", await sessionRail.judged.scopeReason(event, scope, ctx));
	}
	pi.on("tool_call", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		const state = stateFor(ctx);
		state.calls++;
		state.autonomyUses = [];
		let refusal: Refusal | undefined;
		if (loadError) refusal = refuse("rails-config", loadError);
		else try { refusal = await callReason(event, scopeInputSchema.parse(runtimeEvent.input), ctx, state); }
		catch (error) {
			// Only git and gh writes resolve the mode without notifying (structuredAt notifies): they refuse.
			refusal = error instanceof OriginError ? refuse("mode-unresolved", unresolvedText(error))
				: refuse(error instanceof RailError ? error.code : "policy-error", `Agent rails: policy could not be evaluated: ${String(error)}`);
		}
		// An owner's autonomy approval covers one allowed action: a refused call leaves it for the retry.
		if (!refusal) for (const key of state.autonomyUses) approvals.consume(ctx.sessionManager.getSessionId(), key);
		state.autonomyUses = [];
		await ledger.call(loadError ? undefined : map, event, ctx, state, refusal);
		return refusal ? { block: true, reason: refusal.reason } : undefined;
	});
	pi.on("tool_result", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { additionalContext: loadError };
		const state = stateFor(ctx);
		try {
			if (event.toolName === "eval") await trust.evidence.end(event.toolCallId, root => commonDir(pi, root));
			return await recordResult(event, ctx, state, true);
		}
		catch (error) {
			const failure = refuse("post-edit-error", `Agent rails: post-edit policy failed: ${String(error)}`);
			await ledger.write(ctx, state, ctx.cwd, [blockLine("context", event.toolName, failure)]);
			return { additionalContext: failure.reason };
		}
	});
	async function handoffReasons(ctx: ExtensionContext, state: State): Promise<Refusal[]> {
		const reasons: Refusal[] = [];
		let editedDynaflowsRepo = false;
		for (const root of new Set([...state.edits, ...state.pathFailures.keys()])) {
			if (await modeOf(ctx, root) !== "structured") continue;
			if (state.edits.has(root)) editedDynaflowsRepo = true;
			if (!(await acceptedConfig(pi, root))?.gates.pathCheck) state.pathFailures.delete(root);
			const failedPaths = state.pathFailures.get(root);
			const error = failedPaths
				? await ledger.path(ctx, state, root, { code: "handoff-path-check", tool: "session_stop" }, await checkPaths(root, failedPaths, state))
				: undefined;
			if (error) reasons.push(error);
		}
		const urls = editedDynaflowsRepo ? missing(state, map, "structured", ["verification"]) : [];
		if (urls.length) reasons.push(refuse("handoff-read", `read ${urls.join(" and ")}`));
		return reasons;
	}
	pi.on("session_stop", async (_event, ctx) => {
		const state = stateFor(ctx);
		let refusals: Refusal[];
		let reason: string | undefined;
		if (loadError) [refusals, reason] = [[refuse("rails-config", loadError)], loadError];
		else try {
			refusals = await handoffReasons(ctx, state);
			if (refusals.length) reason = `Agent rails: ${refusals.map(refusal => refusal.reason).join("; ")}, then retry handoff.`;
		} catch (error) {
			const failure = error instanceof OriginError
				? refuse("mode-unresolved", unresolvedText(error)) : refuse("handoff-error", `Agent rails: handoff policy failed: ${String(error)}`);
			[refusals, reason] = [[failure], failure.reason];
		}
		await ledger.handoff(ctx, state, refusals);
		return reason === undefined ? undefined : { decision: "block", reason };
	});
	async function structuredOutcome(ctx: ExtensionContext, state: State, notify: Notify, request: StructuredRequest): Promise<Applied> {
		let at: ModeAt;
		try { at = await modeAt(ctx); }
		catch (error) {
			if (!(error instanceof OriginError)) throw error;
			return { notice: notify(unresolvedText(error), "error") };
		}
		if (at.invalid) {
			return { notice: request.notice, content: invalidContent(request.slug, request.path, at.why ?? "invalid config") };
		}
		if (at.mode === "minimal") {
			const minimal = sentence(at.why ?? "dynaflows minimal mode");
			return { notice: notify(`Agent rails: ${minimal}`), content: `${minimal.charAt(0).toUpperCase()}${minimal.slice(1)} Do not apply the dynaflows workflow.` };
		}
		state.pointed = true;
		if (request.task) return { notice: request.notice, content: `Dynaflows structured mode. Route this work with ${workflowReferences(map)}.` };
		return { notice: request.notice, content: startContent(request.slug, workflowReferences(map)) };
	}
	async function applyDynaflows(ctx: ExtensionContext, mode: Mode, task: boolean): Promise<Applied> {
		const state = stateFor(ctx);
		const notify: Notify = (notice, level = "info") => { ctx.ui.notify(notice, level); return notice; };
		let target: { slug: string; config: ConfigState };
		try { target = await directiveTarget(ctx); }
		catch (error) { return { notice: notify(error instanceof OriginError ? unresolvedText(error) : `Agent rails: ${String(error)}`, "error") }; }
		state.pointed = false;
		const { slug, config } = target;
		try {
			await writeMode(modesPath, slug, mode);
			trust.snapshot.accept(modesPath, await fileState(modesPath));
		} catch (error) { return { notice: notify(`Agent rails: cannot set dynaflows for ${slug}: ${String(error)}`, "error") }; }
		await syncRules(ctx);
		if (config.kind !== "ok" && config.kind !== "invalid") return unconfiguredOutcome(notify, slug, config, mode);
		const notice = notify(`Agent rails: dynaflows ${mode} mode for ${slug} (${modesPath}).`);
		if (mode === "minimal") return { notice, content: "Dynaflows minimal mode: fixed guards only; do not apply the dynaflows workflow." };
		return structuredOutcome(ctx, state, notify, { notice, slug, path: config.path, task });
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
		return agentMessage(`Dynaflows structured mode. Route this work with ${workflowReferences(map)}.`);
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
