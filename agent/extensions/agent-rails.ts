import { access, appendFile, lstat, mkdir, readFile, realpath, stat, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, relative, resolve } from "node:path";
import {
	z, type ExtensionAPI, type ExtensionContext, type ToolCallEvent, type ToolResultEvent,
	type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent,
} from "@oh-my-pi/pi-coding-agent";

const stringList = z.array(z.string());
const regexpSource = z.string().refine(value => {
	try { new RegExp(value); return true; } catch { return false; }
});
const mappingSchema = z.object({
	references: z.record(z.string(), stringList).superRefine((references, ctx) => {
		for (const key of ["firstEdit", "newFile", "test", "discovery", "verification", "workflow", "git"]) {
			const urls = references[key];
			if (!urls?.length || urls.some(url => !url.startsWith("skill://"))) {
				ctx.addIssue({ code: "custom", message: `invalid references.${key}` });
			}
		}
	}),
	testGlobs: stringList,
	codeGlobs: stringList,
	discoveryTools: stringList,
	editTools: stringList,
	gitCommand: regexpSource,
	pathFields: z.record(z.string(), stringList),
	resolveDevice: z.string(),
	rejectDevice: z.string(),
});
const argvSchema = z.array(z.string().refine(value => value.trim().length > 0)).min(1);
const policyFields = {
	pathCheck: argvSchema.optional(),
	pathCheckTimeoutMs: z.number().int().positive().max(Number.MAX_SAFE_INTEGER).optional(),
	testGlobs: stringList.optional(),
	agentMerge: z.enum(["allowed", "disabled"]).optional(),
	ciHost: z.enum(["github", "nuc"]).optional(),
};
// `checks.commit` runs in the Git pre-commit hook; `checks.ci` runs on the CI host for push and merge.
const gateSchema = z.object({
	schemaVersion: z.literal(2),
	checks: z.object({ commit: argvSchema.optional(), ci: argvSchema.optional() }).optional(),
	...policyFields,
}).transform(gate => ({
	commit: gate.checks?.commit,
	ci: gate.checks?.ci,
	pathCheck: gate.pathCheck,
	pathCheckTimeoutMs: gate.pathCheckTimeoutMs,
	testGlobs: gate.testGlobs,
	agentMerge: gate.agentMerge,
	ciHost: gate.ciHost,
}));
const pathValueSchema = z.union([z.string(), stringList]).optional().catch(undefined);
const inputFieldsSchema = z.record(z.string(), pathValueSchema).catch({});
const inputSchema = z.object({
	path: z.string().optional().catch(undefined),
	command: z.string().optional().catch(undefined),
	cwd: z.string().optional().catch(undefined),
	input: z.string().optional().catch(undefined),
}).catch({ path: undefined, command: undefined, cwd: undefined, input: undefined });
const scopeSchema = z.object({
	path: z.unknown().optional(), paths: z.unknown().optional(),
}).catch({ path: undefined, paths: undefined });
const answerSchema = z.object({
	question: z.string().optional().catch(undefined),
	selectedOptions: stringList.optional().catch(undefined),
	timedOut: z.boolean().optional().catch(undefined),
});
const detailsSchema = z.object({
	applied: z.boolean().optional().catch(undefined),
	files: stringList.optional().catch(undefined),
	// Ask: single-question fields, or `results` for multi-question calls.
	question: z.string().optional().catch(undefined),
	selectedOptions: stringList.optional().catch(undefined),
	timedOut: z.boolean().optional().catch(undefined),
	results: z.array(answerSchema).optional().catch(undefined),
}).catch({
	applied: undefined, files: undefined, question: undefined, selectedOptions: undefined,
	timedOut: undefined, results: undefined,
});
const dynaflowsSchema = z.object({ schemaVersion: z.literal(1), enabled: z.boolean() });
const proposalSchema = z.object({ schemaVersion: z.literal(1), minimal: stringList, full: stringList });
type Dynaflows = z.infer<typeof dynaflowsSchema>;
type Proposal = z.infer<typeof proposalSchema>;
type Approval = "minimal" | "full";
const fileErrorSchema = z.object({ code: z.string().optional() });
type Mapping = z.infer<typeof mappingSchema>;
type Gate = z.infer<typeof gateSchema>;
type GitAction = {
	command: string; args: string[]; cwd?: string; config: string[];
	// A hook-skip variable the command assigns, exports or unsets anywhere (see `hookSkipVariables`).
	hookSkip?: string;
};
interface State {
	reads: Set<string>;
	notes: Set<string>;
	// Repository roots where this session edited a file matching `codeGlobs`.
	edits: Set<string>;
	pathFailures: Map<string, string[]>;
	authorFamilies: Map<string, string>;
	codegraphAvailable?: boolean;
	discovered: boolean;
	pointed: boolean;
	proposal: string[];
	guardrailApproval?: Approval;
}
type ToolEvent = {
	toolName: string;
	toolCallId: string;
	input: z.infer<typeof inputSchema>;
	pathFields: z.infer<typeof inputFieldsSchema>;
	hasScope: boolean;
	isError?: boolean;
	details: z.infer<typeof detailsSchema>;
};

function policyEvent(event: ToolCallEvent | ToolResultEvent): ToolEvent {
	const scope = scopeSchema.parse(event.input);
	return {
		toolName: event.toolName, toolCallId: event.toolCallId,
		input: inputSchema.parse(event.input), pathFields: inputFieldsSchema.parse(event.input),
		hasScope: scope.path !== undefined || scope.paths !== undefined,
		isError: event.type === "tool_result" ? event.isError : undefined,
		details: detailsSchema.parse(event.type === "tool_result" ? event.details : undefined),
	};
}

function fresh(): State {
	return {
		reads: new Set(), notes: new Set(), edits: new Set(), pathFailures: new Map(),
		authorFamilies: new Map(), discovered: false, pointed: false, proposal: [],
	};
}
function absolute(path: string, cwd: string): string | undefined {
	if (/^[a-z][\w+.-]*:\/\//i.test(path)) return;
	return resolve(cwd, path.startsWith("~/") ? resolve(homedir(), path.slice(2)) : path);
}
async function repoRoot(path: string): Promise<string | undefined> {
	let dir = (await stat(path).catch(() => null))?.isDirectory() ? path : dirname(path);
	for (;;) {
		if (await access(resolve(dir, ".git")).then(() => true, () => false)) return dir;
		const parent = dirname(dir);
		if (dir === parent) return;
		dir = parent;
	}
}
function pathArguments(event: ToolEvent, map: Mapping): string[] {
	const values = (map.pathFields[event.toolName] ?? []).flatMap(field => {
		const value = event.pathFields[field];
		return value === undefined ? [] : Array.isArray(value) ? value : [value];
	});
	// Live edit events have derived paths; persisted assistant calls retain their wire payload.
	if (event.toolName === "edit" && event.input.input !== undefined) {
		for (const match of event.input.input.matchAll(/^\[([^\n]+)#[0-9A-Fa-f]{4}\]$|^\*\*\* (?:Add|Update|Delete|Edit) File: (.+)$|^\*\*\* Move to: (.+)$|^MV (.+)$/gm)) {
			const path = match[1] ?? match[2] ?? match[3] ?? match[4];
			if (path) values.push(path.replace(/^["']|["']$/g, ""));
		}
	}
	return [...new Set(values.flatMap(value => value.split(";")).map(value =>
		value.trim().replace(/:[\d,+-]+(?::raw)?$/, ""),
	))];
}
function paths(event: ToolEvent, map: Mapping, cwd: string): string[] {
	return pathArguments(event, map).flatMap(value => absolute(value, cwd) ?? []);
}
async function subagentPathReason(targets: string[]): Promise<string | undefined> {
	for (const target of targets) {
		const path = target.startsWith("~/") ? resolve(homedir(), target.slice(2)) : target;
		if (/^[a-z][\w+.-]*:\/\//i.test(path)) continue;
		if (!isAbsolute(path)) return `Agent rails: subagent write path "${path}" is relative; use an absolute path inside your assigned worktree.`;
		try {
			const lexical = resolve(path);
			let root = await repoRoot(lexical);
			if (!root) continue;
			let parent = lexical;
			let resolved: string;
			for (;;) {
				try {
					resolved = resolve(await realpath(parent), relative(parent, lexical));
					break;
				} catch (error) {
					const parsed = fileErrorSchema.safeParse(error);
					if (!parsed.success || parsed.data.code !== "ENOENT" || dirname(parent) === parent) throw error;
					parent = dirname(parent);
				}
			}
			// A linked external repository may expose its own .git; also check lexical enclosing roots.
			while (root) {
				const outside = relative(await realpath(root), resolved);
				if (outside === ".." || outside.startsWith("../") || isAbsolute(outside)) {
					return `Agent rails: subagent write path "${path}" resolves to "${resolved}", outside git root "${root}"; use an absolute path inside your assigned worktree without a symlink escape.`;
				}
				const parent = dirname(root);
				root = parent === root ? undefined : await repoRoot(parent);
			}
		} catch {
			// Supplemental filesystem checks fail open; reference gates still apply.
		}
	}
}
function matches(path: string, globs: string[]): boolean {
	return globs.some(glob => new Bun.Glob(glob).match(path));
}
function missing(state: State, map: Mapping, triggers: string[]): string[] {
	return [...new Set(triggers.flatMap(trigger => map.references[trigger] ?? []))]
		.filter(url => !state.reads.has(url));
}
function readReason(urls: string[]): string {
	return `Agent rails: read ${urls.join(" and ")}, then retry.`;
}

const wrapperOptions = {
	sudo: ["-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "--close-from", "-D", "--chdir", "-T", "--command-timeout", "-R", "--chroot"],
	env: ["-u", "--unset", "-C", "--chdir", "-S", "--split-string"],
	time: ["-f", "--format", "-o", "--output"],
	command: [], nohup: [], nice: ["-n", "--adjustment"], exec: ["-a"],
	xargs: ["-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-J", "-L", "--max-lines", "-n", "--max-args", "-P", "--max-procs", "-s", "--max-chars"],
	timeout: ["-k", "--kill-after", "-s", "--signal"],
} satisfies Record<string, string[]>;

// Parse command positions and known launchers, not quoted prose/arguments.
// Dynamic shell expansion is not evaluated: an unresolved directory fails closed for publishing.
// `scan` is shared with nested `sh -c` and `env -S` scripts so a variable touched anywhere marks
// every git action of the whole command.
async function gitActions(command: string, cwd: string | undefined, scan: { hookSkip?: string } = {}): Promise<GitAction[]> {
	const tokens = command.match(/(?:[^\s;&|()"'\\]+|\\.|"(?:\\.|[^"\\])*"|'[^']*')+|&&|\|\||[;&|()\n]/g) ?? [];
	const actions: GitAction[] = [];
	const directory = async (path: string | undefined, from: string | undefined) => {
		if (!path || path === "-" || /[$`*?[\]]/.test(path)) return;
		const target = isAbsolute(path) || path.startsWith("~/") ? absolute(path, "/") : from && resolve(from, path);
		return target && (await stat(target).catch(() => null))?.isDirectory() ? target : undefined;
	};
	const touch = (word: string | undefined) => {
		const name = word?.split("=")[0] ?? "";
		if (Object.hasOwn(hookSkipVariables, name)) scan.hookSkip ??= word;
	};
	let words: string[] = [];
	const flush = async () => {
		while (words.length) {
			while (/^[A-Za-z_]\w*=/.test(words[0] ?? "") || ["then", "do", "else", "{", "!", "if", "while", "until"].includes(words[0] ?? "")) touch(words.shift());
			const launcher = basename(words[0] ?? "");
			if (!Object.hasOwn(wrapperOptions, launcher)) break;
			// SAFETY: hasOwn above restricts the command name to the static lookup's keys.
			const valued: string[] = wrapperOptions[launcher as keyof typeof wrapperOptions];
			words.shift();
			while (words[0]?.startsWith("-")) {
				const option = words.shift()!;
				if (option === "--") break;
				if (launcher === "command" && ["-v", "-V"].includes(option)) { words = []; return; }
				const value = valued.includes(option) ? words.shift() : option.includes("=") ? option.slice(option.indexOf("=") + 1) : undefined;
				if (launcher === "env" && ["-S", "--split-string"].includes(option.split("=")[0])) {
					if (value) actions.push(...await gitActions([value, ...words.map(word => JSON.stringify(word))].join(" "), cwd, scan));
					words = []; return;
				}
				if ((launcher === "env" && ["-C", "--chdir"].includes(option.split("=")[0])) ||
					(launcher === "sudo" && ["-D", "--chdir"].includes(option.split("=")[0]))) cwd = await directory(value, cwd);
				if (launcher === "env" && ["-u", "--unset"].includes(option.split("=")[0])) touch(value);
				else if (launcher === "env" && /^-u./.test(option)) touch(option.slice(2));
			}
			if (launcher === "timeout") words.shift();
		}
		const executable = basename(words.shift() ?? "");
		if (["export", "unset", "declare", "typeset", "readonly", "local"].includes(executable)) words.forEach(touch);
		else if (["bash", "sh", "zsh"].includes(executable)) {
			while (words[0]?.startsWith("-")) {
				const option = words.shift()!;
				if (/^-[^-]*c/.test(option)) {
					if (words[0]) actions.push(...await gitActions(words[0], cwd, scan));
					break;
				}
				if (["-o", "+o", "-O"].includes(option)) words.shift();
			}
		} else if (["cd", "pushd", "popd"].includes(executable)) {
			while (["-L", "-P", "--"].includes(words[0] ?? "")) words.shift();
			cwd = executable === "popd" ? undefined : await directory(words.length === 1 ? words[0] : undefined, cwd);
		} else if (executable === "git") {
			let effective = cwd;
			const config: string[] = [];
			while (words[0]?.startsWith("-")) {
				const option = words.shift()!;
				if (["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"].includes(option)) {
					const value = words.shift();
					if (option === "-C") effective = await directory(value, effective);
					if ((option === "-c" || option === "--config-env") && value) config.push(value);
				} else if (option.startsWith("--config-env=")) config.push(option.slice("--config-env=".length));
				else if (option.startsWith("-c") && option.length > 2) config.push(option.slice(2));
				else if (option.startsWith("-C") && option.length > 2) effective = await directory(option.slice(2), effective);
			}
			actions.push({ command: `git ${words.join(" ")}`, args: [...words], cwd: effective, config });
		} else if (executable === "gh") {
			words = words.filter((word, index, all) =>
				!["-R", "--repo", "--hostname"].includes(word) &&
				!["-R", "--repo", "--hostname"].includes(all[index - 1]) &&
				!/^(-R.|--repo=|--hostname=)/.test(word));
			actions.push({ command: `gh ${words.join(" ")}`, args: [...words], cwd, config: [] });
		}
		words = [];
	};
	const subshells: (string | undefined)[] = [];
	for (const token of tokens) {
		if (/^(?:&&|\|\||[;&|()\n])$/.test(token)) {
			await flush();
			if (token === "(") subshells.push(cwd);
			if (token === ")") cwd = subshells.pop();
		} else words.push(token.replace(/"((?:\\.|[^"\\])*)"|'([^']*)'|\\(.)/g,
			(_match, double: string | undefined, single: string | undefined, escaped: string | undefined) =>
				double?.replace(/\\(["\\$`])/g, "$1") ?? single ?? escaped ?? ""));
	}
	await flush();
	if (scan.hookSkip) for (const action of actions) action.hookSkip ??= scan.hookSkip;
	return actions;
}

// Environment variables that make a hook manager or Git skip the pre-commit hook. Conservatively,
// any assignment, export, unset or `env -u` of one of these names anywhere in a command blocks its
// commits, whatever the value or later shell state. Quoted prose such as a commit message is not
// a command position and does not count.
const hookSkipVariables = {
	LEFTHOOK: true, HUSKY: true, SKIP: true, PRE_COMMIT_ALLOW_NO_CONFIG: true,
	GIT_CONFIG_PARAMETERS: true, GIT_CONFIG_COUNT: true,
} satisfies Record<string, true>;

// `git commit` options that take the next word as their value, so it is not parsed as an option.
const commitValueShorts = { m: true, F: true, c: true, C: true, t: true } satisfies Record<string, true>;
const commitValueLongs = {
	"--message": true, "--file": true, "--reuse-message": true, "--reedit-message": true, "--fixup": true,
	"--squash": true, "--template": true, "--author": true, "--date": true, "--cleanup": true,
	"--trailer": true, "--pathspec-from-file": true,
} satisfies Record<string, true>;
// Returns the first argument, Git config or hook-skip variable that would skip the pre-commit hook.
function commitBypass(action: GitAction): string | undefined {
	const args = action.args.slice(1);
	for (let index = 0; index < args.length; index++) {
		const arg = args[index];
		if (arg === "--") break;
		if (arg.startsWith("--")) {
			const name = arg.split("=")[0];
			// Git accepts unambiguous long-option prefixes, so `--no-ver` means --no-verify and `--mes` --message.
			if (name.length > "--no-".length && "--no-verify".startsWith(name)) return arg;
			if (!arg.includes("=") && name.length > 2 && Object.keys(commitValueLongs).some(long => long.startsWith(name))) index++;
			continue;
		}
		if (!arg.startsWith("-") || arg === "-") continue;
		for (let at = 1; at < arg.length; at++) {
			const flag = arg[at];
			if (flag === "n") return arg;
			// -u and -S take an optional value attached to the same word.
			if (flag === "u" || flag === "S") break;
			if (Object.hasOwn(commitValueShorts, flag)) {
				if (at === arg.length - 1) index++;
				break;
			}
		}
	}
	const hooksPath = action.config.find(entry => entry.split("=")[0].toLowerCase() === "core.hookspath");
	if (hooksPath) return hooksPath;
	return action.hookSkip;
}

// Only authorship crosses factory boundaries. Registry ids connect nested child sessions to their
// parent; all reference reads, checks and approvals remain private to each session.
const authorLineages = new Map<string, Map<string, string>>();

// `machineDynaflowsPath` holds the per-machine default (~/.omp/dynaflows.json); a project's
// .omp/dynaflows.json overrides it; absent both, dynaflows is off.
export default function agentRails(
	pi: ExtensionAPI,
	{ machineDynaflowsPath = fileURLToPath(new URL("../../dynaflows.json", import.meta.url)) }: { machineDynaflowsPath?: string } = {},
): void {
	// Factories are rebound to child sessions; only the parent-keyed authorship registry is shared.
	const states = new Map<string, State>();
	let map: Mapping;
	let loadError: string | undefined;
	try {
		const parsed = mappingSchema.safeParse(Bun.YAML.parse(readFileSync(new URL("../agent-rails.yml", import.meta.url), "utf8")));
		if (!parsed.success) throw new Error("invalid mappings");
		map = parsed.data;
	} catch (error) {
		loadError = `Agent rails: cannot load agent-rails.yml: ${String(error)}`;
	}
	const stateFor = (ctx: ExtensionContext) => {
		const id = ctx.sessionManager.getSessionId();
		let state = states.get(id);
		if (!state) {
			state = fresh();
			const parent = ctx.agent.parentId && authorLineages.get(ctx.agent.parentId);
			if (parent) state.authorFamilies = parent;
			states.set(id, state);
		}
		if (ctx.agent.id) authorLineages.set(ctx.agent.id, state.authorFamilies);
		return state;
	};
	const recordAuthor = (ctx: ExtensionContext, state: State) => {
		if (ctx.agent.parentId && !authorLineages.has(ctx.agent.parentId)) throw new Error("cannot resolve author lineage; start the child from an active parent session");
		const model = ctx.models.current();
		if (!model) throw new Error("cannot resolve the editing agent's model family");
		state.authorFamilies.set(ctx.models.family(model), `${ctx.agent.name ?? ctx.agent.kind} ${ctx.agent.id ?? ctx.sessionManager.getSessionId()}`);
	};
	const note = (state: State, ctx: ExtensionContext, message: string) => {
		if (state.notes.has(message)) return;
		state.notes.add(message);
		ctx.ui.notify(message, "info");
	};
	async function gateAt(root: string, state: State, ctx: ExtensionContext, strict = false): Promise<Gate | undefined> {
		const path = resolve(root, ".omp/gate.json");
		try {
			const parsed = gateSchema.safeParse(JSON.parse(await readFile(path, "utf8")));
			if (!parsed.success) throw new Error(`invalid gate: ${parsed.error.message}`);
			return parsed.data;
		} catch (error) {
			const parsedError = fileErrorSchema.safeParse(error);
			const absent = parsedError.success && parsedError.data.code === "ENOENT";
			if (!absent && strict) throw new Error(`${path}: ${String(error)}`);
			note(state, ctx, `Agent rails: ${path} ${absent ? "absent" : "invalid or unreadable"}; reference checks apply.`);
			return;
		}
	}
	function readJson(path: string, schema: typeof dynaflowsSchema, state: State, ctx: ExtensionContext): Promise<Dynaflows | undefined>;
	function readJson(path: string, schema: typeof proposalSchema, state: State, ctx: ExtensionContext): Promise<Proposal | undefined>;
	async function readJson(
		path: string, schema: typeof dynaflowsSchema | typeof proposalSchema, state: State, ctx: ExtensionContext,
	): Promise<Dynaflows | Proposal | undefined> {
		try {
			const parsed = schema.safeParse(JSON.parse(await readFile(path, "utf8")));
			if (parsed.success) return parsed.data;
			note(state, ctx, `Agent rails: ${path} is invalid; ignored.`);
		} catch (error) {
			const parsedError = fileErrorSchema.safeParse(error);
			if (!parsedError.success || parsedError.data.code !== "ENOENT") note(state, ctx, `Agent rails: ${path} is unreadable; ignored.`);
		}
	}
	const projectRoot = async (ctx: ExtensionContext) => await repoRoot(ctx.cwd) ?? ctx.cwd;
	async function dynaflowsOn(ctx: ExtensionContext): Promise<boolean> {
		const state = stateFor(ctx);
		const project = await readJson(resolve(await projectRoot(ctx), ".omp/dynaflows.json"), dynaflowsSchema, state, ctx);
		return (project ?? await readJson(machineDynaflowsPath, dynaflowsSchema, state, ctx))?.enabled ?? false;
	}
	async function setDynaflows(ctx: ExtensionContext, enabled: boolean): Promise<string> {
		const root = await projectRoot(ctx);
		const path = resolve(root, ".omp/dynaflows.json");
		await mkdir(dirname(path), { recursive: true });
		await writeFile(path, `${JSON.stringify({ schemaVersion: 1, enabled })}\n`);
		// Dynaflows state is local: keep it out of commits without touching tracked ignore files.
		const gitDir = resolve(root, ".git");
		if ((await stat(gitDir).catch(() => null))?.isDirectory()) {
			const exclude = resolve(gitDir, "info/exclude");
			const current = await readFile(exclude, "utf8").catch(() => "");
			if (!current.split("\n").includes("/.omp/dynaflows.json")) {
				await mkdir(dirname(exclude), { recursive: true });
				await appendFile(exclude, `${current && !current.endsWith("\n") ? "\n" : ""}/.omp/dynaflows.json\n`);
			}
		}
		return path;
	}
	async function guardrailReason(targets: string[], ctx: ExtensionContext, state: State): Promise<string | undefined> {
		if (!targets.length || !(await dynaflowsOn(ctx))) return;
		for (const path of targets) {
			const root = await repoRoot(path);
			if (!root) continue;
			const gate = await gateAt(root, state, ctx);
			if (gate?.commit && gate.pathCheck) continue;
			const rel = relative(root, path);
			const proposal = await readJson(resolve(root, ".omp/guardrail-proposal.json"), proposalSchema, state, ctx);
			const isGate = rel === ".omp/gate.json";
			if (!isGate && !proposal?.minimal.includes(rel) && !proposal?.full.includes(rel)) continue;
			const approved = state.guardrailApproval;
			if (approved && (isGate || proposal?.[approved].includes(rel))) continue;
			return approved
				? `Agent rails: owner approved the "${approved}" guardrail option, which does not list ${rel}.`
				: `Agent rails: ${rel} is a guardrail change; get owner sign-off via ask (question mentions "guardrail"; options "Minimal" and "Full") first.`;
		}
	}
	// The pre-commit hook Git would run: respects core.hooksPath; must be an executable file.
	async function commitHookInstalled(cwd: string): Promise<boolean> {
		const result = await pi.exec("git", ["rev-parse", "--git-path", "hooks/pre-commit"], { cwd, timeout: 20000 });
		if (result.code !== 0 || result.killed) throw new Error(`cannot resolve the pre-commit hook: ${result.stderr}`);
		const info = await stat(resolve(cwd, result.stdout.trim())).catch(() => null);
		return Boolean(info?.isFile() && info.mode & 0o111);
	}
	async function checkPaths(root: string, changed: string[], gate: Gate, state: State): Promise<string | undefined> {
		if (!gate.pathCheck) return;
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
			if (changed.length) {
				// exec has no stdin option: pass filenames as positional arguments, never shell source.
				const ignored = await pi.exec("sh", ["-c", 'printf "%s\\0" "$@" | git check-ignore -z --stdin', "agent-rails", ...changed], { cwd: canonicalRoot, timeout: 20000 });
				// Fake/non-Git repositories and Git errors do not exempt any existing path.
				if (!ignored.killed && (ignored.code === 0 || ignored.code === 1)) {
					const excluded = new Set(ignored.stdout.split("\0").filter(Boolean));
					changed = changed.filter(path => !excluded.has(path));
				}
			}
			if (!changed.length) { state.pathFailures.delete(root); return; }
			const result = await pi.exec(gate.pathCheck[0], [...gate.pathCheck.slice(1), ...changed], {
				cwd: canonicalRoot, timeout: gate.pathCheckTimeoutMs ?? 20000,
			});
			if (result.code !== 0 || result.killed) throw new Error(`${result.stdout}\n${result.stderr}`.trim());
			state.pathFailures.delete(root);
		} catch (error) {
			state.pathFailures.set(root, changed);
			return `Agent rails: pathCheck failed in ${root}: ${String(error)}`;
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
		// Owner sign-off for guardrails: an answered (not timed-out) ask about guardrails picking Minimal or Full.
		if (event.toolName === "ask" && event.isError === false) {
			for (const answer of details.results ?? [details]) {
				if (answer.timedOut || !/guardrail/i.test(answer.question ?? "")) continue;
				const pick = /^(minimal|full)\b/i.exec(answer.selectedOptions?.[0] ?? "")?.[1]?.toLowerCase();
				if (pick === "minimal" || pick === "full") state.guardrailApproval = pick;
			}
			return;
		}
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
		if (runCheck && changed.length && event.toolName === "ast_edit") recordAuthor(ctx, state);
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
			const gate = await gateAt(root, state, ctx);
			if (!gate?.pathCheck) continue;
			const changed = [...new Set([...(state.pathFailures.get(root) ?? []), ...changedPaths])];
			const error = await checkPaths(root, changed, gate, state);
			if (error) errors.push(error);
		}
		return errors.length ? { additionalContext: errors.join("\n") } : undefined;
	}
	async function rebuild(_event: SessionStartEvent | SessionSwitchEvent | SessionBranchEvent | SessionTreeEvent, ctx: ExtensionContext): Promise<void> {
		const state = fresh();
		const previous = states.get(ctx.sessionManager.getSessionId());
		state.pointed = previous?.pointed ?? false;
		state.pathFailures = previous?.pathFailures ?? new Map();
		state.codegraphAvailable = previous?.codegraphAvailable;
		state.authorFamilies = previous?.authorFamilies ?? stateFor(ctx).authorFamilies;
		states.set(ctx.sessionManager.getSessionId(), state);
		if (loadError) { note(state, ctx, loadError); return; }
		const calls = new Map<string, ToolEvent>();
		for (const entry of ctx.sessionManager.getBranch()) {
			if (entry.type === "compaction") { state.reads.clear(); state.discovered = false; continue; }
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
	pi.on("session_start", rebuild);
	pi.on("session_switch", rebuild);
	pi.on("session_branch", rebuild);
	pi.on("session_tree", rebuild);
	pi.on("session_compact", (_event, ctx) => {
		const state = stateFor(ctx);
		state.reads.clear(); state.discovered = false;
	});
	pi.on("before_subagent_spawn", async (event, ctx) => {
		if (event.agent !== "verifier") return;
		const state = stateFor(ctx);
		// Core has already expanded agent frontmatter and configured roles into patterns.
		// Empty patterns inherit the parent model; retain every retry family's authorship.
		const models = event.patterns.length
			? event.patterns.map(pattern => ctx.models.resolve(pattern)).filter(model => model !== undefined)
			: [ctx.models.current()].filter(model => model !== undefined);
		if (!models.length) return { block: true, reason: `Agent rails: cannot resolve ${event.agent}'s model family.` };
		// Authors are always recorded so turning dynaflows on mid-session still sees them.
		if (!(await dynaflowsOn(ctx))) return;
		const independent = models.filter(model => !state.authorFamilies.has(ctx.models.family(model)));
		if (!independent.length) return {
			block: true, reason: `Agent rails: verifier must use a different model family from every recorded author: ${[...state.authorFamilies].map(([family, author]) => `${family} (${author})`).join(", ")}. Use an independent family; read-only reviews belong in reviewer/scout/verifier.`,
		};
		if (independent.length !== models.length) return {
			model: independent.map(model => `${model.provider}/${model.id}`),
			note: "Agent rails: removed verifier models from recorded author families.",
		};
	});
	pi.on("tool_call", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { block: true, reason: loadError };
		const state = stateFor(ctx);
		try {
			if (map.discoveryTools.includes(event.toolName) && !state.discovered) {
				state.codegraphAvailable ??= (await pi.exec("sh", ["-c", "command -v codegraph"], { cwd: ctx.cwd, timeout: 20000 })).code === 0;
				const scopes = paths(event, map, ctx.cwd);
				if (!scopes.length && !event.hasScope) scopes.push(ctx.cwd);
				if (state.codegraphAvailable && (await Promise.all(scopes.map(repoRoot))).some(Boolean)) {
					const urls = missing(state, map, ["discovery"]);
					if (urls.length) return { block: true, reason: readReason(urls) };
					state.discovered = true;
				}
			}
			if (event.toolName === "bash" && event.input.command !== undefined) {
				const cwd = absolute(event.input.cwd ?? ctx.cwd, ctx.cwd) ?? ctx.cwd;
				for (const action of await gitActions(event.input.command, cwd)) {
					if (!new RegExp(map.gitCommand).test(action.command)) continue;
					const urls = missing(state, map, ["git"]);
					if (urls.length) return { block: true, reason: readReason(urls) };
					if (/^git commit\b/.test(action.command)) {
						const bypass = commitBypass(action);
						if (bypass) return {
							block: true, reason: `Agent rails: commits must run the pre-commit hook; drop ${bypass} and commit again.`,
						};
						if (!action.cwd) return {
							block: true, reason: "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.",
						};
						const root = await repoRoot(action.cwd);
						const gate = root ? await gateAt(root, state, ctx, true) : undefined;
						if (gate?.commit && !(await commitHookInstalled(action.cwd))) return {
							block: true, reason: `Agent rails: ${root} declares checks.commit but no pre-commit hook is installed; install the repository's hook (e.g. \`pre-commit install\`, \`lefthook install\` or its documented setup), then commit.`,
						};
						recordAuthor(ctx, state);
					}
					if (/^(?:git push\b|gh pr (?:create|merge)\b)/.test(action.command)) {
						if (!action.cwd) return {
							block: true, reason: "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push or gh pr create/merge.",
						};
						const root = await repoRoot(action.cwd);
						const gate = root ? await gateAt(root, state, ctx, true) : undefined;
						if (gate?.agentMerge === "disabled") return {
							block: true, reason: "Agent rails: agentMerge is disabled; ask the owner before git push, gh pr create or gh pr merge in this repository.",
						};
						if (/^gh pr merge\b/.test(action.command) && await dynaflowsOn(ctx)) {
							const args = action.args;
							const at = args.findIndex(arg => arg === "--match-head-commit" || arg.startsWith("--match-head-commit="));
							const head = at < 0 ? undefined : args[at].includes("=") ? args[at].slice(args[at].indexOf("=") + 1) : args[at + 1];
							const reason = `Agent rails: merge requires a valid pass marker for ${head ?? "<missing 40-hex SHA>"}; use --match-head-commit <40-hex> and run prove() or exempt_docs_only() first.`;
							if (!head || !/^[0-9a-f]{40}$/.test(head)) return { block: true, reason };
							const common = await pi.exec("git", ["rev-parse", "--git-common-dir"], { cwd: action.cwd, timeout: 20000 });
							if (common.code !== 0 || common.killed) return { block: true, reason };
							let marker: { schemaVersion?: unknown; head?: unknown; status?: unknown; kind?: unknown } | undefined;
							try { marker = JSON.parse(await readFile(resolve(action.cwd, common.stdout.trim(), "omp-prove", `${head}.json`), "utf8")); } catch { /* Missing or corrupt proof is not a pass. */ }
							if (marker?.schemaVersion !== 1 || marker.head !== head || marker.status !== "pass" || (marker.kind !== "prove" && marker.kind !== "docs-only")) return { block: true, reason };
						}
					}
				}
			}
			if (!map.editTools.includes(event.toolName)) return;
			if (ctx.agent.kind !== "main") {
				const reason = await subagentPathReason([
					...pathArguments(event, map),
					...(event.input.path === map.resolveDevice ? state.proposal : []),
				]);
				if (reason) return { block: true, reason };
			}
			const targets = event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
			const guardrail = await guardrailReason(targets, ctx, state);
			if (guardrail) return { block: true, reason: guardrail };
			const required = new Set<string>();
			for (const path of targets) {
				const root = await repoRoot(path);
				const gate = root ? await gateAt(root, state, ctx) : undefined;
				if (gate?.pathCheck) continue;
				const rel = relative(root ?? ctx.cwd, path);
				const triggers: string[] = [];
				if (!state.edits.size && matches(rel, map.codeGlobs)) triggers.push("firstEdit");
				if (!(await access(path).then(() => true, () => false))) triggers.push("newFile");
				if (matches(rel, gate?.testGlobs ?? map.testGlobs)) triggers.push("test");
				for (const url of missing(state, map, triggers)) required.add(url);
			}
			if (required.size) return { block: true, reason: readReason([...required]) };
			if (targets.length && !(event.toolName === "ast_edit")) recordAuthor(ctx, state);
		} catch (error) { return { block: true, reason: `Agent rails: policy could not be evaluated: ${String(error)}` }; }
	});
	pi.on("tool_result", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { additionalContext: loadError };
		try { return await recordResult(event, ctx, stateFor(ctx), true); }
		catch (error) { return { additionalContext: `Agent rails: post-edit policy failed: ${String(error)}` }; }
	});
	pi.on("session_stop", async (_event, ctx) => {
		if (ctx.agent.kind !== "main") return;
		if (loadError) return { decision: "block", reason: loadError };
		const state = stateFor(ctx);
		try {
			const reasons: string[] = [];
			const roots = new Set([...state.edits, ...state.pathFailures.keys()]);
			for (const root of roots) {
				const gate = await gateAt(root, state, ctx);
				const failedPaths = state.pathFailures.get(root);
				if (failedPaths && gate?.pathCheck) {
					const error = await checkPaths(root, failedPaths, gate, state);
					if (error) reasons.push(error);
				}
			}
			// Repository checks run at commit (pre-commit hook) and in CI, never at handoff.
			const urls = state.edits.size ? missing(state, map, ["verification"]) : [];
			if (urls.length) reasons.push(`read ${urls.join(" and ")}`);
			if (reasons.length) {
				return { decision: "block", reason: `Agent rails: ${reasons.join("; ")}, then retry handoff.` };
			}
		} catch (error) { return { decision: "block", reason: `Agent rails: handoff policy failed: ${String(error)}` }; }
	});
	pi.on("before_agent_start", async (event, ctx) => {
		if (loadError || ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const state = stateFor(ctx);
		const keyword = /(?:^|\s)\$dynaflows(?:\s+(on|off))?(?=\s|$)/i.exec(event.prompt ?? "");
		if (keyword) {
			const enabled = keyword[1]?.toLowerCase() !== "off";
			const path = await setDynaflows(ctx, enabled);
			ctx.ui.notify(`Agent rails: dynaflows ${enabled ? "on" : "off"} (${path}).`, "info");
			state.pointed = false;
			if (!enabled) return { message: {
				customType: "agent-rails-dynaflows", content: "Dynaflows off: do not apply omp-dyna-workflows.",
				display: false, attribution: "agent",
			} };
		}
		if (state.pointed || !(await dynaflowsOn(ctx))) return;
		state.pointed = true;
		const lines = [`Dynaflows on. Route this work with ${map.references.workflow.join(" and ")}.`];
		const root = await repoRoot(ctx.cwd);
		const gate = root ? await gateAt(root, state, ctx) : undefined;
		if (root && !(gate?.commit && gate.pathCheck)) lines.push(
			`Guardrails missing in ${root}: .omp/gate.json lacks checks.commit or pathCheck.`,
			"Before guardrail work, ask the owner whether to analyse and propose guardrails.",
			"If yes: write .omp/guardrail-proposal.json {\"schemaVersion\":1,\"minimal\":[repo-relative paths],\"full\":[repo-relative paths]};",
			"minimal = least needed for checks.commit, pathCheck and an installed pre-commit hook; full = every recommended guardrail.",
			"Present both, then call ask with a question containing \"guardrail\" and options \"Minimal\" and \"Full\".",
			"Writes to .omp/gate.json and proposal paths stay blocked until the owner picks one.",
		);
		return { message: {
			customType: "agent-rails-dynaflows", content: lines.join(" "), display: false, attribution: "agent",
		} };
	});
	pi.registerCommand("extension-health-agent-rails", {
		description: "Agent rails registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Agent rails registered", loadError ? "error" : "info"); },
	});
}
