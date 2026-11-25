import { lstat, readFile, realpath } from "node:fs/promises";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import {
	z, type ExtensionAPI, type ExtensionContext,
	type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent,
} from "@oh-my-pi/pi-coding-agent";
import {
	commonDir, configRoot as defaultConfigRoot, OriginError, originSlug, repoSlug, resolveConfig, unresolvedText,
	type AgentRailsConfig, type Ci, type ConfigState,
} from "./lib/repo-config.ts";
import {
	SUBPROCESS_TIMEOUT_MS, loadMapping, detailsSchema, policyEvent, absolute, repoRoot,
	paths, pathArguments, gitActions, note, fileErrorSchema,
	codeRunsGit, INDEX_WRITES, commitScopeArgument,
	recordRead, subagentPathReason, type GitAction, type Mapping, type State as SharedState, type ToolEvent,
	blockLine, closeEpisodes, coded, insertEntries, openEpisode, RailError, refuse, resolvedLine, storePath as defaultStorePath,
	type Episode, type LedgerLine, type RailCode, type Refusal,
} from "./lib/rails-runtime.ts";
import {
	PUBLISHES, adminMergeReason, autonomyReason, commitHookReason, configWriteReason, hookBypassReason, mergeMethodReason, mergePolicyFor,
	prCreateBranch, prMerge, prMergeReason, pushedBranches, pushReason, routeReason, worktreeReason, type AutonomyApprovals, type DefaultBranches,
} from "./lib/guards.ts";
import {
	Approvals, defaultReviewsPath, ompJudge, registryDecide, scopeInputSchema, sessionRails, type JudgeTransport, type ObligationSource,
	type PrStatus,
} from "./lib/session-rails.ts";
import { registerOwnerSurface } from "./lib/onboarding.ts";
import {
	COMMIT_CHECKS_TIMEOUT_MS, Evidence, Snapshot, evalReason, pinSettings, protectedPathReason, shellStateReason,
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
type AgentRailsApi = Pick<ExtensionAPI, "on" | "exec" | "registerCommand" | "registerTool" | "sendUserMessage"> & { pi: Pick<ExtensionAPI["pi"], "settings"> };
const toolReply = (text: string) => ({ content: [{ type: "text" as const, text }], details: {} });
interface State extends SharedState {
	pathFailures: Map<string, string[]>;
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
		reads: new Set(), notes: new Set(), pathFailures: new Map(),
		proposal: [], calls: 0, episodes: [], autonomyUses: [], onboarding: new Set(),
	};
}
// A rebuilt session state keeps the path failures, call count, open ledger episodes and onboarding.
function carried(previous: State | undefined): State {
	const state = fresh();
	if (!previous) return state;
	return {
		...state, pathFailures: previous.pathFailures, calls: previous.calls, episodes: previous.episodes,
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
// The plain docs refusals cite by path.
const proofsReference = join(import.meta.dir, "references", "proofs-and-merge.md");
const judgmentsReference = join(import.meta.dir, "references", "judgments.md");
// A commit whose checks print SECURITY.dependency-audit lines is triaged, never suppressed.
const dependencyAuditNote =
	"SECURITY.dependency-audit: triage every advisory with dependency_audit_findings(repo), then " +
	"triage_advisories(found, evidence) in an eval cell. evidence maps each advisory key to exactly advisory_text, " +
	"advisory_source, scope (runtime or dev), scope_source, code_search {queries, hits: [{location, line}]}, fix_options " +
	"and blocked_fix (null or {blocked_by, constraint, constraint_source}). On halt give the owner the report; " +
	`otherwise upgrade each fix entry and commit again. Never add or edit audit exceptions. See ${judgmentsReference}#dependency-advisories.`;
const harnessOnlyNote =
	"commands.commit passed. Checks come from .agentrails/config.json commands; the owner changes them through the " +
	"setup-repo-proofs writer. If a repo checker already enforces a failing step's rules, do not reshape repo code to " +
	"the harness copy: report it to the owner, who can give the step to the repo checker (checkers.check.repo).";

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
	if (!parsesAs(result, text)) return refuse("merge-prove", `Agent rails: no prove() pass for ${head} at ${path}; run prove() for this head, then merge (${proofsReference}).`);
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
	catch { return refuse("merge-structure", `Agent rails: no valid structure_review() result for ${head} at ${path}; run structure_review() for this head, then merge (${proofsReference}).`); }
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
async function groupByRoot(changed: string[]): Promise<Map<string, string[]>> {
	const groups = new Map<string, string[]>();
	for (const path of changed) {
		const root = await repoRoot(path);
		if (!root) continue;
		groups.set(root, [...(groups.get(root) ?? []), path]);
	}
	return groups;
}
/** Why a config state is not one the rails can run: the engine's problems, or why it could not be resolved. */
function configFault(state: Exclude<ConfigState, { kind: "ok" | "missing" }>): string {
	return state.kind === "invalid"
		? `the agent-rails config ${state.path} is invalid: ${state.problems.join("; ")}`
		: `cannot resolve this repository's agent-rails config: ${state.reason}`;
}
/**
 * What the rails run for a directory (railsScope). outside: no Git repository or no GitHub origin, so
 * fixed guards only. unresolved: the config cannot be resolved, so git and gh writes are refused.
 * unonboarded: a missing or invalid config with no onboarding in progress, so ordinary work is
 * refused until the owner onboards. onboarding: the owner runs /agentrails onboard in this session.
 * governed: a config the engine accepted; every rail runs, gated by its gates and autonomy.
 */
type RailsScope =
	| { kind: "outside" }
	| { kind: "unresolved"; reason: string }
	| { kind: "unonboarded"; slug: string; state: Extract<ConfigState, { kind: "missing" | "invalid" }> }
	| { kind: "onboarding"; slug: string; main: string }
	| { kind: "governed"; config: AgentRailsConfig; main: string };
type Unonboarded = Extract<RailsScope, { kind: "unonboarded" }>;
function onboardingRefusal(scope: Unonboarded): Refusal {
	const { slug, state } = scope;
	const why = state.kind === "missing" ? "missing" : `invalid: ${state.problems.join("; ")}`;
	return refuse(state.kind === "missing" ? "onboarding-required" : "config-invalid",
		`Agent rails: ${slug} is not onboarded (${why}). Ask the owner to run /agentrails onboard; it runs setup-repo-proofs and ` +
		"test-guidance, then a short Q&A that writes .agentrails/config.json.",
		`Agent rails: ${slug} is not onboarded (${state.kind}).`);
}
// A commit, push, PR or merge in a repository that is not onboarded, once the fixed guards passed it.
const onboardingReason = (scope: RailsScope): Refusal | undefined => scope.kind === "unonboarded" ? onboardingRefusal(scope) : undefined;
// A merge before the repository is governed: refused while it is not onboarded, and while the owner
// onboards it, since the owner merges its bootstrap PR.
const onboardingMergeReason = (scope: RailsScope): Refusal | undefined => scope.kind === "onboarding"
	? refuse("onboarding-merge", `Agent rails: ${scope.slug} is onboarding, so gh pr merge is refused: the owner merges the bootstrap PR.`)
	: onboardingReason(scope);
// The shape of a gh pr merge, checked before any scope: no --admin, known flags, one PR and one 40-hex --match-head-commit.
function mergeFormatReason(args: string[]): Refusal | undefined {
	const merge = prMerge(args);
	return adminMergeReason(args) ?? prMergeReason(merge) ?? headFormatReason(merge.heads[0]);
}
const unresolvedRefusal = (reason: string): Refusal => refuse("config-unresolved", unresolvedText(reason));
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
	throw new RailError(state.kind === "invalid" ? "config-invalid" : "config-unresolved", configFault(state));
}
// The config of a repository whose rails run, when the engine accepted it; undefined for any other state.
async function acceptedConfig(pi: ExecApi, root: string): Promise<AgentRailsConfig | undefined> {
	const state = await configAt(pi, root);
	return state.kind === "ok" ? state.config : undefined;
}
// The repo config's autonomy.branch for each branch a git push sends, once pushReason passed it.
async function branchAutonomyReason(pi: ExecApi, action: GitAction, config: AgentRailsConfig | undefined, approved: AutonomyApprovals): Promise<Refusal | undefined> {
	if (!action.cwd || config?.autonomy.branch !== "ask") return undefined;
	const cwd = action.cwd;
	for (const branch of await pushedBranches(pi, cwd, action.args)) {
		const reason = autonomyReason({ action: "branch", subject: branch }, config, approved);
		if (reason) return reason;
	}
	return undefined;
}
type SessionCache = { builtins?: Promise<Set<string>> };
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
	const audit = output.includes("SECURITY.dependency-audit") ? `\n${dependencyAuditNote}` : "";
	return refuse(run.code, `${failed}; ` +
		`fix every finding in the repo, then commit again:\n${output.length > commitOutputLimit ? `…${output.slice(-commitOutputLimit)}` : output}${audit}${repoOwned ? "" : `\n${harnessOnlyNote}`}`, failed);
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
// The rail state a session trusts: the session repo's origin. The repo's agent-rails config is not
// snapshotted: agents cannot write it, and the rails re-read it on every action.
async function captureState(snapshot: Snapshot, cwd: string, notify: (text: string, level: "error") => void): Promise<void> {
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
	if (!reason && unresolved) note(state, ctx, unresolvedText(unresolved.message));
	return reason;
}
function mergeAloneReason(actions: GitAction[]): Refusal | undefined {
	return actions.length > 1 && actions.some(action => /^gh pr merge\b/.test(action.command))
		? refuse("merge-alone", "Agent rails: run gh pr merge as its own bash command, so the head the rails check is the head gh merges.")
		: undefined;
}
// unresolved: the origin remote could not be read for this bash call, so every git or gh write in it is refused.
type Step = { action: GitAction; staged: boolean; unresolved?: OriginError | undefined };
// The staged checks (handoff, commit checks, implementation-integrity) read the index before the command runs,
// so the committed tree must be that index.
function committedIndexReason(step: Step, scope: RailsScope): Refusal | undefined {
	if (step.staged) return refuse("commit-after-staging", "Agent rails: run git commit in a command of its own after staging; the rails check the index as it is before the command runs.");
	const argument = scope.kind === "governed" ? commitScopeArgument(step.action.args) : undefined;
	return argument === undefined ? undefined
		: refuse("commit-scope", `Agent rails: commits commit the staged index only, which the commit checks see; stage with git add, then run git commit without -a, -i, -o, -p or paths (got ${argument}).`);
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
type ScopeInput = Parameters<ReturnType<typeof sessionRails>["judged"]["scopeReason"]>[1];

type AgentRailsOptions = {
	configPath: string; configRoot: string; guardrailsCommand: string[]; guardrailsConfigCheckCommand: string[];
	ompCommand: string[]; extensionDir: string; reviewsPath: string; obligationSources: ObligationSource[];
	/** The decision store the rails ledger writes (lib/rails-ledger.ts); never a repository. */
	storePath: string;
	decideCommand: string[];
	/** The judge transport for judged rails; omp's judge role unless a test stubs it. */
	judge: ((ctx: ExtensionContext) => JudgeTransport) | undefined;
};
function defaultOptions(): AgentRailsOptions {
	return {
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

export default function agentRails(pi: AgentRailsApi, options: Partial<AgentRailsOptions> = {}): void {
	const {
		configPath, configRoot, guardrailsCommand, guardrailsConfigCheckCommand, ompCommand, extensionDir, reviewsPath, obligationSources,
		decideCommand, judge, storePath,
	}: AgentRailsOptions = { ...defaultOptions(), ...options };
	// A ledger row's repository at cwd; one that cannot be resolved (no origin) is recorded as null.
	const ledger = new RailsLedger(storePath, async cwd => {
		const root = await repoRoot(cwd).catch(() => undefined);
		return { repo: root ? await repoSlug(root).catch(() => null) : null };
	}), states = new Map<string, State>(), session: SessionCache = {}, approvals = new Approvals(), trust: Trust = {
		evidence: new Evidence(), snapshot: new Snapshot(), defaults: new Map(),
		protection: {
			reviewsFile: reviewsPath, extensionDir: dirname(configPath),
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
		governed: async (ctx, cwd) => {
			const scope = await railsScope(ctx, cwd);
			if (scope.kind === "unresolved") throw new OriginError(scope.reason);
			return scope.kind === "governed";
		},
		inGitRepo: async cwd => await repoRoot(cwd) !== undefined,
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
	// The rails' scope for the repository holding cwd: the config state, and onboarding in progress in this session.
	async function railsScope(ctx: ExtensionContext, cwd: string): Promise<RailsScope> {
		const state = await configAt(pi, cwd);
		if (state.kind === "outside") return { kind: "outside" };
		if (state.kind === "unresolved") return { kind: "unresolved", reason: state.reason };
		if (state.kind === "ok") return { kind: "governed", config: state.config, main: state.main };
		let slug: string | null;
		try { slug = await originSlug(pi, state.main); }
		catch (error) { return { kind: "unresolved", reason: error instanceof Error ? error.message : String(error) }; }
		if (slug === null) return { kind: "unresolved", reason: `${state.main} lost its GitHub origin remote` };
		return stateFor(ctx).onboarding.has(state.main) ? { kind: "onboarding", slug, main: state.main } : { kind: "unonboarded", slug, state };
	}
	// For actions that write nothing through git or gh: an unresolved scope is noted, and the action
	// goes on under the fixed guards. Git and gh writes refuse it instead.
	async function notedScope(ctx: ExtensionContext, cwd: string): Promise<RailsScope> {
		const scope = await railsScope(ctx, cwd);
		if (scope.kind === "unresolved") note(stateFor(ctx), ctx, unresolvedText(scope.reason));
		return scope;
	}
	// The onboarding refusal for an edit target inside a repository that is not onboarded.
	async function onboardingEditReason(event: ToolEvent, ctx: ExtensionContext, state: State): Promise<Refusal | undefined> {
		if (!map.editTools.includes(event.toolName)) return undefined;
		const targets = event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
		for (const path of targets) {
			const root = await repoRoot(path);
			const scope = root ? await notedScope(ctx, root) : undefined;
			if (scope?.kind === "unonboarded") return onboardingRefusal(scope);
		}
		return undefined;
	}
	// The onboarding refusal for an askFirst command run inside a repository that is not onboarded.
	async function onboardingAskFirstReason(event: ToolEvent, ctx: ExtensionContext): Promise<Refusal | undefined> {
		const cwd = event.input.cwd ?? ctx.cwd;
		if (!await sessionRail.judged.askFirstCommand(event, cwd)) return undefined;
		const root = await repoRoot(absolute(cwd, ctx.cwd) ?? ctx.cwd);
		const scope = root ? await notedScope(ctx, root) : undefined;
		return scope?.kind === "unonboarded" ? onboardingRefusal(scope) : undefined;
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
	async function commitChecksReason(root: string, config: AgentRailsConfig): Promise<Refusal | undefined> {
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
			const scope = await notedScope(ctx, root);
			if (scope.kind !== "governed") continue;
			if (!scope.config.gates.pathCheck) {
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
		const groups = await groupByRoot(editedPaths(map, event, ctx, state));
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
	pi.on("session_start", async (event, ctx) => {
		await rebuild(event, ctx);
		if (loadError) return;
		pinSettings(pi.pi.settings);
		await captureState(trust.snapshot, ctx.cwd, (text, level) => ctx.ui.notify(text, level));
	});
	pi.on("session_switch", rebuild);
	pi.on("session_branch", rebuild);
	pi.on("session_tree", rebuild);
	pi.on("session_compact", (_event, ctx) => {
		const state = stateFor(ctx);
		state.reads.clear();
	});
	// A governed repository's commit checks: one change over a fully staged index, then the gates and implementation-integrity.
	async function commitActionReason(action: GitAction, scope: RailsScope, ctx: ExtensionContext, call: BashCall): Promise<Refusal | undefined> {
		if (!action.cwd) return refuse("cwd-unresolved", "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.");
		if (scope.kind === "onboarding") ctx.ui.notify(`Agent rails: ${scope.slug} is onboarding; commit checks run once .agentrails/config.json exists.`, "info");
		const root = await repoRoot(action.cwd);
		if (!root || scope.kind !== "governed") return undefined;
		return committedTreeReason(action, root, call) ?? (await unstagedReason(pi, action, root)) ?? (await commitChecksReason(root, scope.config)) ??
			coded("judged-integrity", await sessionRail.judged.commitReason(root, action, ctx));
	}
	// The merge checks once the merge's shape passed (mergeFormatReason): the PR, autonomy.merge, the merge policy and completion-evidence.
	async function mergeActionReason(action: GitAction, state: State, ctx: ExtensionContext): Promise<Refusal | undefined> {
		if (!action.cwd) throw new Error("cannot resolve PR action cwd");
		const merge = prMerge(action.args);
		const head = merge.heads[0];
		if (!head) throw new Error("cannot resolve the merge head");
		const selector = merge.selectors[0];
		const pr = await prCi(action.cwd, selector);
		if (!("head" in pr)) return pr.reason;
		const autonomy = autonomyReason({ action: "merge", subject: String(pr.number) }, pr.config, autonomyApprovals(ctx, state));
		if (autonomy) return autonomy;
		return (await policyMergeReason(pi, action.args, { cwd: action.cwd, head, evidence: trust.evidence }, pr)) ??
			coded("judged-evidence", await sessionRail.judged.mergeReason(action.cwd, selector, head, ctx));
	}
	// Publishing: git push (default-branch block, then autonomy.branch), gh pr create (autonomy.pr) and
	// gh pr merge (refused while the repository onboards, else the merge checks with autonomy.merge).
	async function publishActionReason(action: GitAction, scope: RailsScope, state: State, ctx: ExtensionContext): Promise<Refusal | undefined> {
		if (!action.cwd) return refuse("cwd-unresolved", "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push or gh pr create/merge.");
		const approved = autonomyApprovals(ctx, state);
		const config = scope.kind === "governed" ? scope.config : undefined;
		if (/^git push\b/.test(action.command)) {
			return (await pushReason(pi, action, trust.defaults)) ?? onboardingReason(scope) ?? branchAutonomyReason(pi, action, config, approved);
		}
		if (/^gh pr create\b/.test(action.command)) return onboardingReason(scope) ?? prCreateReason(action, config, approved);
		return mergeFormatReason(action.args) ?? onboardingMergeReason(scope) ?? mergeActionReason(action, state, ctx);
	}
	async function prCreateReason(action: GitAction, config: AgentRailsConfig | undefined, approved: AutonomyApprovals): Promise<Refusal | undefined> {
		return config?.autonomy.pr === "ask" ? autonomyReason({ action: "pr", subject: await prCreateBranch(pi, action) }, config, approved) : undefined;
	}
	async function gitActionReason(step: Step, state: State, ctx: ExtensionContext, call: BashCall): Promise<Refusal | undefined> {
		const { action } = step;
		const bypass = hookBypassReason(action) ?? routeReason(action) ?? await configWriteReason(action);
		if (bypass || !new RegExp(map.gitCommand).test(action.command)) return bypass;
		if (step.unresolved) return unresolvedRefusal(step.unresolved.message);
		const scope: RailsScope = action.cwd ? await railsScope(ctx, action.cwd) : { kind: "outside" };
		if (scope.kind === "unresolved") return unresolvedRefusal(scope.reason);
		return coded("extension-review", await sessionRail.gitWriteReason(action, ctx)) ?? gitWriteKindReason(step, scope, state, ctx, call);
	}
	async function gitWriteKindReason(step: Step, scope: RailsScope, state: State, ctx: ExtensionContext, call: BashCall): Promise<Refusal | undefined> {
		const { action } = step;
		if (/^git worktree add\b/.test(action.command)) return worktreeReason(pi, ompCommand, action);
		if (/^git commit\b/.test(action.command)) {
			return commitHookReason(action) ?? committedIndexReason(step, scope) ?? coded("handoff-staged", await sessionRail.commitReason(action)) ??
				onboardingReason(scope) ?? await commitActionReason(action, scope, ctx, call);
		}
		return PUBLISHES.test(action.command) ? publishActionReason(action, scope, state, ctx) : undefined;
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
	async function callReason(event: ToolEvent, scope: ScopeInput, ctx: ExtensionContext, state: State): Promise<Refusal | undefined> {
		return (await bashReason(event, ctx, state)) ?? (await evalToolReason(trust, event, ctx.cwd)) ??
			protectedEditReason(map, trust.protection, event, ctx.cwd, state) ?? (await subagentReason(map, event, ctx, state)) ??
			(await onboardingEditReason(event, ctx, state)) ?? (await onboardingAskFirstReason(event, ctx)) ??
			coded("judged-scope", await sessionRail.judged.scopeReason(event, scope, ctx));
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
			// An origin or config that cannot be resolved refuses the call.
			refusal = error instanceof OriginError ? unresolvedRefusal(error.message)
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
		for (const root of state.pathFailures.keys()) {
			const scope = await railsScope(ctx, root);
			if (scope.kind === "unresolved") reasons.push(unresolvedRefusal(scope.reason));
			if (scope.kind !== "governed") continue;
			if (!scope.config.gates.pathCheck) state.pathFailures.delete(root);
			const failedPaths = state.pathFailures.get(root);
			const error = failedPaths
				? await ledger.path(ctx, state, root, { code: "handoff-path-check", tool: "session_stop" }, await checkPaths(root, failedPaths, state))
				: undefined;
			if (error) reasons.push(error);
		}
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
				? unresolvedRefusal(error.message) : refuse("handoff-error", `Agent rails: handoff policy failed: ${String(error)}`);
			[refusals, reason] = [[failure], failure.reason];
		}
		await ledger.handoff(ctx, state, refusals);
		return reason === undefined ? undefined : { decision: "block", reason };
	});
	const agentRailsToolParams = z.object({
		rollout: z.literal(true).optional().describe("true when the user asked to roll this work out to every device"),
		acknowledge: z.literal(true).optional().describe("true once you reviewed an agent-rails extension change the rails asked you to review"),
	});
	pi.registerTool({
		name: "agentrails",
		label: "Agent rails",
		description: "Opt this work into device rollout, or acknowledge a reviewed extension change. " +
			"Pass rollout only when the user asked for it in this conversation, in any wording; never on your own initiative. " +
			"Pass acknowledge only after reviewing an extension change the rails asked you to review.",
		parameters: agentRailsToolParams,
		loadMode: "essential",
		approval: "write",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			if (loadError) return toolReply(loadError);
			if (ctx.agent.kind !== "main") return toolReply("Agent rails: only the main agent can call the agentrails tool.");
			const { rollout, acknowledge } = agentRailsToolParams.parse(params);
			const replies = [
				rollout ? await sessionRail.optInRollout(ctx) : undefined,
				acknowledge ? await sessionRail.acknowledge(ctx) : undefined,
			].filter(reply => reply !== undefined);
			return toolReply(replies.join("\n") || "Agent rails: pass rollout or acknowledge.");
		},
	});
	pi.registerCommand("extension-health-agent-rails", {
		description: "Agent rails registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Agent rails registered", loadError ? "error" : "info"); },
	});
}
