import { dirname, isAbsolute, join } from "node:path";
import { type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { DEFAULT_MERGE_POLICY, parseOrigin, type MergePolicy } from "./repo-record.ts";
import type { Mode } from "./modes.ts";
import {
	SUBPROCESS_TIMEOUT_MS, absolute, canonicalPath, commitBypass, readRecord, repoRoot, worktreeTarget, type GitAction,
} from "./rails-runtime.ts";

type ExecApi = Pick<ExtensionAPI, "exec">;
type PushPlan = { remote: string; refspecs: string[]; all: boolean };
type PushOptions = { all: boolean; repository: string | undefined; positional: string[] };
type GhApiRequest = { endpoint: string; method: string; fields: string[] };

const PULLS_COMMITS = /^git (?:merge|rebase|cherry-pick|revert|am|pull)(?![-\w])/;
const PUBLISHES = /^(?:git push\b|gh pr (?:create|merge)\b)/;
const PUSH_VALUED = ["-o", "--push-option", "--repo", "--receive-pack", "--exec"];
const PUSH_EVERY_BRANCH = ["--all", "--mirror", "--branches"];
const EVERY_BRANCH = Symbol("every branch");
type PushTarget = string | typeof EVERY_BRANCH | undefined;
const GH_API_FIELDS = ["-f", "--raw-field", "-F", "--field", "--input"];
const GH_API_VALUED = [...GH_API_FIELDS, "-X", "--method", "-H", "--header", "-q", "--jq", "-t", "--template", "--cache", "-p", "--preview"];
const GH_API_BRANCH_WRITES = [/(?:^|\/)pulls\/[^/]+\/merge\/?$/, /(?:^|\/)merges\/?$/, /(?:^|\/)git\/refs(?:\/|$)/];
const GRAPHQL_BRANCH_MUTATIONS = /\b(?:mergePullRequest|enablePullRequestAutoMerge|mergeBranch|updateRefs?|createRef|deleteRef|createCommitOnBranch)\b/;

const validBase = (value: string | undefined): value is string =>
	value !== undefined && (isAbsolute(value) || value.startsWith("~/"));

function worktreeTargetReason(args: string[], cwd: string, base: string, name: string): string | undefined {
	const target = worktreeTarget(args);
	if (!target) throw new Error("git worktree add requires a target path");
	if (/[$`]/.test(target)) return `Agent rails: received unexpanded shell text \`${target}\`; rails check the literal command, so pass the worktree path itself`;
	const resolved = absolute(target, cwd);
	if (!resolved) throw new Error("git worktree add requires a filesystem target");
	if (dirname(canonicalPath(resolved)) !== canonicalPath(join(base, name))) {
		return `Agent rails: create worktrees at ${base}/${name}/<slug>; got ${resolved}.`;
	}
	return undefined;
}

function commitHookReason(action: GitAction): string | undefined {
	const bypass = commitBypass(action);
	if (bypass) return `Agent rails: commits must run the pre-commit hook; drop ${bypass} and commit again.`;
	if (!action.cwd) return "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.";
	return undefined;
}

function hookBypassReason(action: GitAction): string | undefined {
	if (action.hookSkip) return `Agent rails: pre-commit hook environment mutation is forbidden: ${action.hookSkip}`;
	if (!PULLS_COMMITS.test(action.command)) return undefined;
	const bypass = commitBypass(action);
	return bypass ? `Agent rails: drop hook bypass ${bypass}` : undefined;
}

async function configuredBase(pi: ExecApi, ompCommand: string[], cwd: string): Promise<string | undefined> {
	const environmentBase = process.env["OMP_WORKTREE_DIR"];
	if (validBase(environmentBase)) return environmentBase;
	const [command, ...prefix] = ompCommand;
	if (command === undefined) throw new Error("omp command is empty");
	const result = await pi.exec(command, [...prefix, "config", "get", "worktree.base"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.code !== 0 || result.killed) throw new Error(`cannot read worktree.base: ${result.stderr}`);
	return result.stdout.trim();
}

async function gitText(pi: ExecApi, cwd: string, args: string[]): Promise<string | undefined> {
	const result = await pi.exec("git", args, { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
	if (result.killed) throw new Error(`git ${args.join(" ")} was killed`);
	return result.code === 0 ? result.stdout.trim() : undefined;
}

async function worktreeReason(pi: ExecApi, ompCommand: string[], action: GitAction): Promise<string | undefined> {
	if (!action.cwd) throw new Error("cannot resolve git worktree add effective cwd");
	const base = await configuredBase(pi, ompCommand, action.cwd);
	const expandedBase = validBase(base) ? absolute(base, action.cwd) : undefined;
	if (!expandedBase || !isAbsolute(expandedBase)) return "Agent rails: worktree.base is unset; set it (agent/config.yml) before git worktree add.";
	const origin = await gitText(pi, action.cwd, ["remote", "get-url", "origin"]);
	if (origin === undefined) return "Agent rails: no origin remote; cannot create a worktree.";
	const name = parseOrigin(origin)?.split("/")[1];
	if (!name) return `Agent rails: origin ${origin} is not a GitHub owner/name URL; cannot create a worktree.`;
	return worktreeTargetReason(action.args, action.cwd, expandedBase, name);
}

async function agentMergeReason(action: GitAction, configRoot: string): Promise<string | undefined> {
	if (!action.cwd) return "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push or gh pr create/merge.";
	const root = await repoRoot(action.cwd);
	if (!root) throw new Error("publishing requires a validated local Git repository");
	const record = await readRecord(root, configRoot);
	if (record?.gate.agentMerge === "disabled") return "Agent rails: agentMerge is disabled; ask the owner before git push, gh pr create or gh pr merge in this repository.";
	return undefined;
}

function pushOption(args: string[], index: number, options: PushOptions): number {
	const arg = args[index] ?? "";
	options.all ||= PUSH_EVERY_BRANCH.includes(arg);
	if (arg.startsWith("--repo=")) options.repository = arg.slice("--repo=".length);
	else if (arg === "--repo") options.repository = args[index + 1];
	return arg === "--repo" || PUSH_VALUED.includes(arg) ? index + 1 : index;
}

function pushPlan(args: string[]): PushPlan {
	const options: PushOptions = { all: false, repository: undefined, positional: [] };
	for (let index = 1; index < args.length; index++) {
		const arg = args[index] ?? "";
		if (arg === "--") { options.positional.push(...args.slice(index + 1)); break; }
		if (arg.startsWith("-")) index = pushOption(args, index, options);
		else options.positional.push(arg);
	}
	const named = options.repository === undefined ? options.positional : [options.repository, ...options.positional];
	const [remote = "origin", ...refspecs] = named;
	return { remote, refspecs, all: options.all };
}

function branchName(ref: string, current: string | undefined): string | undefined {
	const name = ref === "HEAD" || ref === "@" ? current : ref;
	const short = name?.startsWith("refs/") ? name.slice("refs/".length) : name;
	return short?.startsWith("heads/") ? short.slice("heads/".length) : short;
}

function destination(refspec: string, current: string | undefined): string | typeof EVERY_BRANCH | undefined {
	const spec = refspec.replace(/^\+/, "");
	if (spec.startsWith("^")) return undefined;
	if (spec === ":" || spec.includes("*")) return EVERY_BRANCH;
	return branchName(spec.includes(":") ? spec.slice(spec.indexOf(":") + 1) : spec, current);
}

async function gitLines(pi: ExecApi, cwd: string, args: string[]): Promise<string[]> {
	return (await gitText(pi, cwd, args))?.split("\n").filter(Boolean) ?? [];
}

async function implicitTargets(pi: ExecApi, cwd: string, remote: string, current: string | undefined): Promise<PushTarget[]> {
	const configured = await gitLines(pi, cwd, ["config", "--get-all", `remote.${remote}.push`]);
	if (configured.length) return configured.map(refspec => destination(refspec, current));
	if (await gitText(pi, cwd, ["config", "--get", "push.default"]) === "matching") return [EVERY_BRANCH];
	const upstream = current ? await gitText(pi, cwd, ["config", "--get", `branch.${current}.merge`]) : undefined;
	return [current, upstream === undefined ? undefined : branchName(upstream, current)];
}

async function pushReason(pi: ExecApi, action: GitAction): Promise<string | undefined> {
	if (!action.cwd) return "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push.";
	const plan = pushPlan(action.args);
	const current = await gitText(pi, action.cwd, ["symbolic-ref", "--short", "-q", "HEAD"]);
	const targets = plan.refspecs.length
		? plan.refspecs.map(refspec => destination(refspec, current))
		: await implicitTargets(pi, action.cwd, plan.remote, current);
	const head = await gitText(pi, action.cwd, ["symbolic-ref", "--short", `refs/remotes/${plan.remote}/HEAD`]);
	if (!head?.startsWith(`${plan.remote}/`)) {
		return `Agent rails: cannot resolve the default branch of remote ${plan.remote} (refs/remotes/${plan.remote}/HEAD); ` +
			`run git remote set-head ${plan.remote} --auto, then push again.`;
	}
	const branch = head.slice(plan.remote.length + 1);
	if (!plan.all && !targets.includes(EVERY_BRANCH) && !targets.includes(branch)) return undefined;
	return `Agent rails: git push to ${plan.remote}'s default branch ${branch} is blocked in minimal and structured mode; ` +
		"push a feature branch and open a PR instead.";
}

function configKey(entry: string): string {
	return (entry.split("=")[0] ?? "").toLowerCase();
}

function gitRouteReason(action: GitAction): string | undefined {
	if (!action.command.startsWith("git ")) return undefined;
	const alias = action.config.map(configKey).find(key => key.startsWith("alias."));
	if (alias) return `Agent rails: git -c ${alias} defines a command the rails cannot check; run the Git command itself.`;
	if (/^git send-pack\b/.test(action.command)) return "Agent rails: git send-pack bypasses the push checks; use git push.";
	if (!/^git push\b/.test(action.command)) return undefined;
	const override = action.config.map(configKey).find(key => /^(?:push|remote|branch)\./.test(key));
	return override
		? `Agent rails: git -c ${override} changes where git push sends commits; drop it and push with the repository's own configuration.`
		: undefined;
}

function ghApiOption(arg: string): [string, string | undefined] {
	if (arg.startsWith("--")) {
		const at = arg.indexOf("=");
		return at < 0 ? [arg, undefined] : [arg.slice(0, at), arg.slice(at + 1)];
	}
	return [arg.slice(0, 2), arg.length > 2 ? arg.slice(2) : undefined];
}

function ghApiValue(request: GhApiRequest, name: string, value: string): void {
	if (name === "-X" || name === "--method") request.method = value.toUpperCase();
	if (GH_API_FIELDS.includes(name)) request.fields.push(value);
}

function ghApiRequest(args: string[]): GhApiRequest {
	const request: GhApiRequest = { endpoint: "", method: "", fields: [] };
	for (let index = 1; index < args.length; index++) {
		const arg = args[index] ?? "";
		if (!arg.startsWith("-")) {
			request.endpoint ||= arg;
			continue;
		}
		const [name, inline] = ghApiOption(arg);
		if (!GH_API_VALUED.includes(name)) continue;
		ghApiValue(request, name, inline ?? args[++index] ?? "");
	}
	return request;
}

function ghApiReason(action: GitAction): string | undefined {
	if (!/^gh api\b/.test(action.command)) return undefined;
	const request = ghApiRequest(action.args);
	const endpoint = (request.endpoint.startsWith("/") ? request.endpoint.slice(1) : request.endpoint).split("?")[0] ?? "";
	const method = request.method || (request.fields.length ? "POST" : "GET");
	const mutation = endpoint === "graphql" && request.fields.some(field => GRAPHQL_BRANCH_MUTATIONS.test(field));
	const write = !["GET", "HEAD"].includes(method) && GH_API_BRANCH_WRITES.some(pattern => pattern.test(endpoint));
	return mutation || write
		? `Agent rails: gh api ${method} ${endpoint} merges or moves branches outside the rails; use gh pr merge or git push.`
		: undefined;
}

function minimalMergeReason(mode: Mode): string | undefined {
	return mode === "minimal"
		? "Agent rails: gh pr merge is blocked in dynaflows minimal mode; the owner merges, or switches this repository to structured mode."
		: undefined;
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

type AppliedPolicy = Omit<MergePolicy, "base" | "headPrefix"> & { source: string };
const METHOD_FLAGS = new Map([["--rebase", "rebase"], ["-r", "rebase"], ["--merge", "merge"], ["-m", "merge"], ["--squash", "squash"], ["-s", "squash"]]);

function adminMergeReason(args: string[]): string | undefined {
	return args.some(arg => arg === "--admin" || arg.startsWith("--admin="))
		? "Agent rails: gh pr merge --admin bypasses the base branch's requirements; drop --admin and merge once they pass."
		: undefined;
}

/** The record's first mergePolicies entry for the PR's base and head branch, else the default. */
function mergePolicyFor(policies: MergePolicy[], base: string, head: string): AppliedPolicy {
	const match = policies.find(policy => policy.base === base && head.startsWith(policy.headPrefix));
	if (!match) return { ...DEFAULT_MERGE_POLICY, source: "the default policy" };
	return { method: match.method, proof: match.proof, source: `mergePolicies {base: ${match.base}, headPrefix: ${match.headPrefix}}` };
}

function mergeMethodReason(args: string[], policy: AppliedPolicy): string | undefined {
	const methods = [...new Set(args.flatMap(arg => METHOD_FLAGS.get(arg) ?? []))];
	if (methods.length === 1 && methods[0] === policy.method) return undefined;
	const given = methods.length ? methods.join(" and ") : "no method";
	return `Agent rails: ${policy.source} merges with --${policy.method}; got ${given}. Run gh pr merge with --${policy.method}.`;
}

function routeReason(action: GitAction): string | undefined {
	return gitRouteReason(action) ?? ghApiReason(action);
}

export { adminMergeReason, mergeMethodReason, mergePolicyFor, prSelector, type AppliedPolicy };
export { PUBLISHES, agentMergeReason, commitHookReason, hookBypassReason, routeReason, minimalMergeReason, pushReason, worktreeReason };
