import { dirname, isAbsolute, join } from "node:path";
import { type ExtensionAPI } from "@oh-my-pi/pi-coding-agent";
import { parseOrigin, type Mode } from "./repo-record.ts";
import {
	SUBPROCESS_TIMEOUT_MS, absolute, canonicalPath, commitBypass, mentionsGit, readRecord, repoRoot, worktreeTarget, type GitAction,
} from "./rails-runtime.ts";

type ExecApi = Pick<ExtensionAPI, "exec">;
type PushPlan = { remote: string | undefined; refspecs: string[]; all: boolean };
type PushOptions = { all: boolean; repository: string | undefined; positional: string[] };
type GhApiRequest = { endpoint: string; method: string; fields: string[]; input: boolean };

const PULLS_COMMITS = /^git (?:merge|rebase|cherry-pick|revert|am|pull)(?![-\w])/;
const PUBLISHES = /^(?:git push\b|gh pr (?:create|merge)\b)/;
const PUSH_VALUED = ["-o", "--push-option", "--repo", "--receive-pack", "--exec"];
const PUSH_EVERY_BRANCH = ["--all", "--mirror", "--branches"];
const EVERY_BRANCH = Symbol("every branch");
type PushTarget = string | typeof EVERY_BRANCH | undefined;
const GH_API_FIELDS = ["-f", "--raw-field", "-F", "--field", "--input"];
const MERGE_BOOLEAN = new Set(["--admin", "--auto", "--delete-branch", "--disable-auto", "--merge", "--rebase", "--squash", "--help", "-d", "-m", "-r", "-s", "-h"]);
const MERGE_VALUED = new Set(["--author-email", "--body", "--body-file", "--subject", "--match-head-commit", "-A", "-b", "-F", "-t"]);
const GH_API_VALUED = [...GH_API_FIELDS, "-X", "--method", "-H", "--header", "-q", "--jq", "-t", "--template", "--cache", "-p", "--preview"];
// The only gh api writes agents make: PR and issue conversation. Everything else (merges, refs,
// contents, statuses, check runs, protection, settings) is refused.
const GH_API_WRITES = [
	/^repos\/[^/]+\/[^/]+\/issues\/\d+\/(?:comments|labels)$/, /^repos\/[^/]+\/[^/]+\/issues\/comments\/\d+$/,
	/^repos\/[^/]+\/[^/]+\/pulls\/\d+\/(?:comments|reviews|requested_reviewers)$/, /^repos\/[^/]+\/[^/]+\/pulls\/\d+\/comments\/\d+\/replies$/,
	/^repos\/[^/]+\/[^/]+\/pulls\/comments\/\d+$/, /^repos\/[^/]+\/[^/]+\/pulls\/\d+\/reviews\/\d+(?:\/events)?$/,
];
const GRAPHQL_MUTATIONS = new Set(["addComment", "addPullRequestReview", "addPullRequestReviewComment", "addPullRequestReviewThread",
	"addPullRequestReviewThreadReply", "submitPullRequestReview", "resolveReviewThread", "unresolveReviewThread", "minimizeComment",
	"updateIssueComment", "updatePullRequestReviewComment", "addReaction", "removeReaction"]);
const GH_COMMANDS = new Set(["api", "attestation", "auth", "browse", "cache", "co", "codespace", "completion", "config", "gist",
	"gpg-key", "help", "issue", "label", "org", "pr", "preview", "project", "release", "repo", "ruleset", "run", "search", "secret",
	"ssh-key", "status", "variable", "workflow", "version"]);
const GH_REFUSED = new Set(["auth token", "config set", "repo set-default", "repo edit", "repo rename", "repo delete", "repo archive",
	"secret set", "secret delete", "variable set", "variable delete", "workflow disable"]);

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
	const [remote, ...refspecs] = named;
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

// The remote a bare git push uses: branch.<b>.pushRemote, remote.pushDefault, branch.<b>.remote, then origin.
async function implicitRemote(pi: ExecApi, cwd: string, current: string | undefined): Promise<string> {
	for (const key of [...(current ? [`branch.${current}.pushRemote`] : []), "remote.pushDefault", ...(current ? [`branch.${current}.remote`] : [])]) {
		const value = await gitText(pi, cwd, ["config", "--get", key]);
		if (value) return value;
	}
	return "origin";
}

// Default branches seen per repository and remote: the first one seen stays protected for the
// session even if the local refs/remotes/<remote>/HEAD is later moved.
type DefaultBranches = Map<string, string>;

async function protectedBranches(pi: ExecApi, cwd: string, remote: string, defaults: DefaultBranches): Promise<string[] | undefined> {
	const head = await gitText(pi, cwd, ["symbolic-ref", "--short", `refs/remotes/${remote}/HEAD`]);
	if (!head?.startsWith(`${remote}/`)) return undefined;
	const key = `${await gitText(pi, cwd, ["rev-parse", "--path-format=absolute", "--git-common-dir"])}\0${remote}`;
	const branch = head.slice(remote.length + 1);
	if (!defaults.has(key)) defaults.set(key, branch);
	return [...new Set([defaults.get(key) ?? branch, branch])];
}

async function pushReason(pi: ExecApi, action: GitAction, defaults: DefaultBranches): Promise<string | undefined> {
	if (!action.cwd) return "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push.";
	const plan = pushPlan(action.args);
	const current = await gitText(pi, action.cwd, ["symbolic-ref", "--short", "-q", "HEAD"]);
	const remote = plan.remote ?? await implicitRemote(pi, action.cwd, current);
	const targets = plan.refspecs.length
		? plan.refspecs.map(refspec => destination(refspec, current))
		: await implicitTargets(pi, action.cwd, remote, current);
	const branches = await protectedBranches(pi, action.cwd, remote, defaults);
	if (!branches) {
		return `Agent rails: cannot resolve the default branch of remote ${remote} (refs/remotes/${remote}/HEAD); ` +
			`run git remote set-head ${remote} --auto, then push again.`;
	}
	const mirror = await gitText(pi, action.cwd, ["config", "--bool", "--get", `remote.${remote}.mirror`]) === "true";
	const hit = branches.find(branch => targets.includes(branch));
	if (!plan.all && !mirror && !targets.includes(EVERY_BRANCH) && !hit) return undefined;
	return `Agent rails: git push to ${remote}'s default branch ${hit ?? branches[0]} is blocked in minimal and structured mode; ` +
		"push a feature branch and open a PR instead.";
}

function configKey(entry: string): string {
	return (entry.split("=")[0] ?? "").toLowerCase();
}

// The config keys an agent may set, one-shot (-c) or persistently: none steers hooks, aliases,
// includes, remotes, branches or what git push sends.
const ALLOWED_CONFIG = /^(?:(?:user|color|advice|diff|log|format|pager|column|i18n|status|grep|blame|pretty)\.|(?:merge\.conflictstyle|commit\.gpgsign|tag\.gpgsign|init\.defaultbranch|safe\.directory|core\.pager|core\.quotepath|core\.editor)$)/;
const ALLOWED_CONFIG_TEXT = "user.*, color.*, advice.*, diff.*, log.*, format.*, pager.* and display keys";
// Commands that create commits or move refs without git commit or git push, so no rail checks them.
const PLUMBING = /^git (?:commit-tree|fast-import|update-ref|replay|history|filter-branch|filter-repo|subtree|replace|send-pack|http-push|receive-pack)(?![-\w])/;
const REMOTE_WRITES = ["add", "set-url", "remove", "rm", "rename", "set-head", "set-branches"];
const CONFIG_WRITE_FLAGS = ["--add", "--unset", "--unset-all", "--replace-all"];
const CONFIG_READ_FLAGS = ["--get", "--get-all", "--get-regexp", "--get-urlmatch", "-l", "--list", "--get-color", "--get-colorbool"];
const CONFIG_VALUED = ["-f", "--file", "--blob", "-t", "--type", "--default", "--comment", "--value"];

function oneShotConfigReason(action: GitAction): string | undefined {
	const keys = action.config.map(configKey);
	const alias = keys.find(key => key.startsWith("alias."));
	if (alias) return `Agent rails: git -c ${alias} defines a command the rails cannot check; run the Git command itself.`;
	const pushing = keys.find(key => /^(?:push|remote|branch)\./.test(key));
	if (pushing && /^git push\b/.test(action.command)) {
		return `Agent rails: git -c ${pushing} changes where git push sends commits; drop it and push with the repository's own configuration.`;
	}
	// Commits and commit-pulling commands name the hook bypass themselves (commitHookReason, hookBypassReason).
	if (keys.includes("core.hookspath")) return /^git commit\b/.test(action.command) || PULLS_COMMITS.test(action.command) ? undefined : "Agent rails: git -c core.hooksPath skips hooks; drop it.";
	const other = keys.find(key => !ALLOWED_CONFIG.test(key));
	return other ? `Agent rails: git -c ${other} is refused; one-shot config may set only ${ALLOWED_CONFIG_TEXT}.` : undefined;
}

type ConfigArgs = { positional: string[]; write: boolean; unnamed: boolean; read: boolean };
const CONFIG_UNNAMED = ["-e", "--edit", "--rename-section", "--remove-section"];

function configArgs(args: string[]): ConfigArgs {
	const parsed: ConfigArgs = { positional: [], write: false, unnamed: false, read: false };
	for (let index = 1; index < args.length; index++) {
		const arg = args[index] ?? "";
		parsed.read ||= CONFIG_READ_FLAGS.includes(arg);
		parsed.unnamed ||= CONFIG_UNNAMED.includes(arg);
		parsed.write ||= CONFIG_WRITE_FLAGS.includes(arg);
		if (CONFIG_VALUED.includes(arg)) index++;
		else if (!arg.startsWith("-")) parsed.positional.push(arg);
	}
	return parsed;
}

// The key a git config command writes, "*" for a write the rails cannot name, or undefined for a read.
function configWriteKey(args: string[]): string | undefined {
	const { positional, write, unnamed, read } = configArgs(args);
	const [first = "", key = "*"] = positional;
	if (unnamed || ["edit", "rename-section", "remove-section"].includes(first)) return "*";
	if (read || ["get", "list"].includes(first)) return undefined;
	if (["set", "unset"].includes(first)) return key.toLowerCase();
	return write || positional.length > 1 ? first.toLowerCase() : undefined;
}

function persistentConfigReason(action: GitAction): string | undefined {
	if (action.args[0] !== "config") return undefined;
	const key = configWriteKey(action.args);
	return key !== undefined && !ALLOWED_CONFIG.test(key)
		? `Agent rails: git config writes to ${key === "*" ? "unnamed keys" : key} are refused (hooks, aliases, includes, remotes and branches steer what the rails check); ask the owner.`
		: undefined;
}

function symbolicRefWrite(args: string[]): boolean {
	return args.filter(arg => !arg.startsWith("-")).length > 2 || args.some(arg => ["-d", "--delete"].includes(arg));
}

// Command text a git subcommand runs itself: rebase --exec, submodule foreach, bisect run.
function nestedCommand(args: string[]): string {
	const [operation = "", sub = "", ...rest] = args;
	if (operation === "rebase") return rebaseExec(args);
	return ["submodule foreach", "bisect run"].includes(`${operation} ${sub}`) ? rest.join(" ") : "";
}

// git remote set-head --auto reads the default branch from the remote itself, so it stays allowed.
function remoteWrite(args: string[]): boolean {
	const [operation = "", sub = ""] = args;
	return operation === "remote" && REMOTE_WRITES.includes(sub) && !(sub === "set-head" && args.some(arg => ["-a", "--auto"].includes(arg)));
}

function stateWriteReason(action: GitAction): string | undefined {
	const [operation = "", sub = ""] = action.args;
	if (/^git (?:send-pack|http-push|receive-pack)\b/.test(action.command)) return `Agent rails: git ${operation} bypasses the push checks; use git push.`;
	if (PLUMBING.test(action.command)) return `Agent rails: git ${operation} creates commits or moves refs outside the rails; use git commit and git push.`;
	if (remoteWrite(action.args)) return `Agent rails: git remote ${sub} changes which repository and default branch the rails check; ask the owner.`;
	if (operation === "symbolic-ref" && symbolicRefWrite(action.args)) return "Agent rails: git symbolic-ref writes move refs the push checks read; ask the owner.";
	return mentionsGit(nestedCommand(action.args)) ? `Agent rails: git ${operation} would run git or gh commands the rails cannot check; run them directly.` : undefined;
}

function rebaseExec(args: string[]): string {
	const values: string[] = [];
	for (let index = 1; index < args.length; index++) {
		const arg = args[index] ?? "";
		if (arg === "-x" || arg === "--exec") values.push(args[++index] ?? "");
		else if (arg.startsWith("--exec=")) values.push(arg.slice("--exec=".length));
		else if (/^-[^-]*x./.test(arg)) values.push(arg.slice(arg.indexOf("x") + 1));
	}
	return values.join(" ");
}

function gitRouteReason(action: GitAction): string | undefined {
	if (!action.command.startsWith("git ")) return undefined;
	return oneShotConfigReason(action) ?? persistentConfigReason(action) ?? stateWriteReason(action);
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
	if (name === "--input") request.input = true;
	else if (GH_API_FIELDS.includes(name)) request.fields.push(value);
}

function ghApiRequest(args: string[]): GhApiRequest {
	const request: GhApiRequest = { endpoint: "", method: "", fields: [], input: false };
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

// The endpoint GitHub receives: no host, query or fragment, percent-decoded, single slashes, lowercase.
function apiEndpoint(endpoint: string): string {
	let path = endpoint.replace(/^https?:\/\/[^/]+/i, "").replace(/[?#].*$/s, "");
	try { path = decodeURIComponent(path); } catch { path = "%"; }
	return path.replace(/\/+/g, "/").replace(/^\/|\/$/g, "").toLowerCase();
}

function graphqlReason(request: GhApiRequest): string | undefined {
	if (request.input || request.fields.some(field => field.startsWith("query=@"))) {
		return "Agent rails: gh api graphql with --input or query=@file sends a body the rails cannot read; pass the query inline.";
	}
	const query = request.fields.filter(field => field.startsWith("query=")).join("\n");
	if (!/\bmutation\b/.test(query)) return undefined;
	const body = query.slice(query.search(/\bmutation\b/)).replace(/^mutation\s*(?:[A-Za-z_]\w*)?/, "");
	const refused = [...body.matchAll(/\b([A-Za-z_]\w*)\s*\(/g)]
		.map(match => match[1] ?? "").filter(name => !GRAPHQL_MUTATIONS.has(name));
	return refused.length ? `Agent rails: gh api graphql mutation ${refused.join(", ")} is outside the allowed PR conversation mutations.` : undefined;
}

function ghApiReason(action: GitAction): string | undefined {
	if (!/^gh api\b/.test(action.command)) return undefined;
	const request = ghApiRequest(action.args);
	if (/[$`\0]/.test(request.endpoint)) return `Agent rails: gh api endpoint ${request.endpoint} is built at run time; pass the literal endpoint.`;
	const endpoint = apiEndpoint(request.endpoint);
	if (endpoint === "graphql") return graphqlReason(request);
	const method = request.method || (request.fields.length || request.input ? "POST" : "GET");
	if (["GET", "HEAD"].includes(method) || GH_API_WRITES.some(pattern => pattern.test(endpoint))) return undefined;
	return `Agent rails: gh api ${method} ${endpoint} is refused; agents may write only PR and issue comments, reviews and labels through gh api.`;
}

function ghCommandReason(action: GitAction): string | undefined {
	if (!action.command.startsWith("gh ")) return undefined;
	const [command = "", sub = ""] = action.args;
	if (!command.startsWith("-") && !GH_COMMANDS.has(command)) {
		return `Agent rails: gh ${command} is not a built-in gh command; gh aliases and extensions run commands the rails cannot check.`;
	}
	return GH_REFUSED.has(`${command} ${sub}`)
		? `Agent rails: gh ${command} ${sub} changes repository settings or targets the rails depend on; ask the owner.`
		: undefined;
}

function minimalMergeReason(mode: Mode): string | undefined {
	return mode === "minimal"
		? "Agent rails: gh pr merge is blocked in dynaflows minimal mode; the owner merges, or switches this repository to structured mode."
		: undefined;
}

function routeReason(action: GitAction): string | undefined {
	return gitRouteReason(action) ?? ghCommandReason(action) ?? ghApiReason(action);
}

type PrMerge = { selectors: string[]; heads: string[]; unknown?: string };

function mergeOption(merge: PrMerge, name: string, value: string | undefined): void {
	if (name === "--match-head-commit") merge.heads.push(value ?? "");
}

// Expands a short-flag cluster such as -ds or -tSubject; returns how many following words it consumed.
function shortCluster(merge: PrMerge, arg: string): number {
	for (let at = 1; at < arg.length; at++) {
		const flag = `-${arg[at]}`;
		if (MERGE_VALUED.has(flag)) return at === arg.length - 1 ? 1 : 0;
		if (!MERGE_BOOLEAN.has(flag)) { merge.unknown = arg; return 0; }
	}
	return 0;
}

function longOption(merge: PrMerge, arg: string, next: string | undefined): number {
	const [name = "", inline] = arg.includes("=") ? [arg.slice(0, arg.indexOf("=")), arg.slice(arg.indexOf("=") + 1)] : [arg, undefined];
	if (MERGE_BOOLEAN.has(name) && inline === undefined) return 0;
	if (!MERGE_VALUED.has(name)) { merge.unknown = arg; return 0; }
	mergeOption(merge, name, inline ?? next);
	return inline === undefined ? 1 : 0;
}

// gh pr merge arguments, parsed with gh's own flag table so the rails check the PR and head gh uses.
function prMerge(args: string[]): PrMerge {
	const merge: PrMerge = { selectors: [], heads: [] };
	for (let index = 2; index < args.length; index++) {
		const arg = args[index] ?? "";
		if (arg === "--") { merge.selectors.push(...args.slice(index + 1)); break; }
		if (arg.startsWith("--")) index += longOption(merge, arg, args[index + 1]);
		else if (arg.startsWith("-") && arg.length > 1) index += shortCluster(merge, arg);
		else merge.selectors.push(arg);
	}
	return merge;
}

function prMergeReason(merge: PrMerge): string | undefined {
	if (merge.unknown) return `Agent rails: gh pr merge flag ${merge.unknown} is not one the rails know; use gh's documented merge flags.`;
	if (merge.heads.length > 1) return "Agent rails: gh pr merge takes one --match-head-commit; gh uses the last, so pass exactly one.";
	return merge.selectors.length > 1 ? "Agent rails: gh pr merge takes one PR selector." : undefined;
}

export { prMerge, prMergeReason, type DefaultBranches };
export { PUBLISHES, agentMergeReason, commitHookReason, hookBypassReason, routeReason, minimalMergeReason, pushReason, worktreeReason };
