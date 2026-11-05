import { access, appendFile, lstat, mkdir, readFile, readlink, realpath, stat, writeFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
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
const gateSchema = z.object({
	schemaVersion: z.literal(1),
	gate: argvSchema.optional(),
	commitGate: argvSchema.optional(),
	fastGate: argvSchema.optional(),
	pathCheck: argvSchema.optional(),
	pathCheckTimeoutMs: z.number().int().positive().max(Number.MAX_SAFE_INTEGER).optional(),
	testGlobs: stringList.optional(),
	sensitive: stringList.optional(),
	agentMerge: z.enum(["allowed", "disabled"]).optional(),
	ciHost: z.enum(["github", "nuc"]).optional(),
});
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
	exitCode: z.number().optional().catch(undefined),
	applied: z.boolean().optional().catch(undefined),
	files: stringList.optional().catch(undefined),
	// Bash: `async` marks a backgrounded start; `timedOut` an unfinished run.
	async: z.unknown().optional().catch(undefined),
	timedOut: z.boolean().optional().catch(undefined),
	// Ask: single-question fields, or `results` for multi-question calls.
	question: z.string().optional().catch(undefined),
	selectedOptions: stringList.optional().catch(undefined),
	results: z.array(answerSchema).optional().catch(undefined),
}).catch({
	exitCode: undefined, applied: undefined, files: undefined, async: undefined, timedOut: undefined,
	question: undefined, selectedOptions: undefined, results: undefined,
});
const modeSchema = z.object({ schemaVersion: z.literal(1), enabled: z.boolean() });
const proposalSchema = z.object({ schemaVersion: z.literal(1), minimal: stringList, full: stringList });
type Mode = z.infer<typeof modeSchema>;
type Proposal = z.infer<typeof proposalSchema>;
type Approval = "minimal" | "full";
const pullRequestSchema = z.object({ number: z.number().int().positive(), url: z.string() });
const fileErrorSchema = z.object({ code: z.string().optional() });
type Mapping = z.infer<typeof mappingSchema>;
type Gate = z.infer<typeof gateSchema>;
interface State {
	reads: Set<string>;
	notes: Set<string>;
	edits: Map<string, number>;
	passed: Map<string, number>;
	bashStarts: Map<string, { root: string; revision: number; fingerprint?: string }>;
	baselines: Map<string, string>;
	checkedTrees: Map<string, string>;
	pathFailures: Map<string, string[]>;
	authorFamilies: Set<string>;
	revision: number;
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
		reads: new Set(), notes: new Set(), edits: new Map(), passed: new Map(),
		bashStarts: new Map(), baselines: new Map(), checkedTrees: new Map(), pathFailures: new Map(), revision: 0,
		authorFamilies: new Set(),
		discovered: false, pointed: false, proposal: [],
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
		if (!isAbsolute(path)) return `Skill gate: subagent write path "${path}" is relative; use an absolute path inside your assigned worktree.`;
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
					return `Skill gate: subagent write path "${path}" resolves to "${resolved}", outside git root "${root}"; use an absolute path inside your assigned worktree without a symlink escape.`;
				}
				const parent = dirname(root);
				root = parent === root ? undefined : await repoRoot(parent);
			}
		} catch {
			// Like fingerprinting, supplemental filesystem checks fail open; reference gates still apply.
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
	return `Skill gate: read ${urls.join(" and ")}, then retry.`;
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
async function gitActions(command: string, cwd: string | undefined): Promise<{ command: string; cwd?: string; ghArgs?: string[] }[]> {
	const tokens = command.match(/(?:[^\s;&|()"'\\]+|\\.|"(?:\\.|[^"\\])*"|'[^']*')+|&&|\|\||[;&|()\n]/g) ?? [];
	const actions: { command: string; cwd?: string; ghArgs?: string[] }[] = [];
	const directory = async (path: string | undefined, from: string | undefined) => {
		if (!path || path === "-" || /[$`*?[\]]/.test(path)) return;
		const target = isAbsolute(path) || path.startsWith("~/") ? absolute(path, "/") : from && resolve(from, path);
		return target && (await stat(target).catch(() => null))?.isDirectory() ? target : undefined;
	};
	let words: string[] = [];
	const flush = async () => {
		while (words.length) {
			while (/^[A-Za-z_]\w*=/.test(words[0] ?? "") || ["then", "do", "else", "{", "!", "if", "while", "until"].includes(words[0] ?? "")) words.shift();
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
					if (value) actions.push(...await gitActions([value, ...words.map(word => JSON.stringify(word))].join(" "), cwd));
					words = []; return;
				}
				if ((launcher === "env" && ["-C", "--chdir"].includes(option.split("=")[0])) ||
					(launcher === "sudo" && ["-D", "--chdir"].includes(option.split("=")[0]))) cwd = await directory(value, cwd);
			}
			if (launcher === "timeout") words.shift();
		}
		const executable = basename(words.shift() ?? "");
		if (["bash", "sh", "zsh"].includes(executable)) {
			while (words[0]?.startsWith("-")) {
				const option = words.shift()!;
				if (/^-[^-]*c/.test(option)) {
					if (words[0]) actions.push(...await gitActions(words[0], cwd));
					break;
				}
				if (["-o", "+o", "-O"].includes(option)) words.shift();
			}
		} else if (["cd", "pushd", "popd"].includes(executable)) {
			while (["-L", "-P", "--"].includes(words[0] ?? "")) words.shift();
			cwd = executable === "popd" ? undefined : await directory(words.length === 1 ? words[0] : undefined, cwd);
		} else if (executable === "git") {
			let effective = cwd;
			while (words[0]?.startsWith("-")) {
				const option = words.shift()!;
				if (["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"].includes(option)) {
					const value = words.shift();
					if (option === "-C") effective = await directory(value, effective);
				} else if (option.startsWith("-C") && option.length > 2) effective = await directory(option.slice(2), effective);
			}
			actions.push({ command: `git ${words.join(" ")}`, cwd: effective });
		} else if (executable === "gh") {
			const ghArgs = [...words];
			words = words.filter((word, index, all) =>
				!["-R", "--repo", "--hostname"].includes(word) &&
				!["-R", "--repo", "--hostname"].includes(all[index - 1]) &&
				!/^(-R.|--repo=|--hostname=)/.test(word));
			actions.push({ command: `gh ${words.join(" ")}`, cwd, ghArgs });
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
	return actions;
}

// `machineModePath` holds the per-machine default (~/.omp/dyna-mode.json); a project's
// .omp/dyna-mode.json overrides it; absent both, dyna mode is off.
export default function skillGate(
	pi: ExtensionAPI,
	{ machineModePath = fileURLToPath(new URL("../../dyna-mode.json", import.meta.url)) }: { machineModePath?: string } = {},
): void {
	// Factories are rebound to child sessions; never share mutable policy state at module scope.
	const states = new Map<string, State>();
	let map: Mapping;
	let loadError: string | undefined;
	try {
		const parsed = mappingSchema.safeParse(Bun.YAML.parse(readFileSync(new URL("../skill-gates.yml", import.meta.url), "utf8")));
		if (!parsed.success) throw new Error("invalid mappings");
		map = parsed.data;
	} catch (error) {
		loadError = `Skill gate: cannot load skill-gates.yml: ${String(error)}`;
	}
	const stateFor = (ctx: ExtensionContext) => {
		const id = ctx.sessionManager.getSessionId();
		let state = states.get(id);
		if (!state) { state = fresh(); states.set(id, state); }
		return state;
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
			note(state, ctx, `Skill gate: ${path} ${absent ? "absent" : "invalid or unreadable"}; reference gates apply.`);
			return;
		}
	}
	function readJson(path: string, schema: typeof modeSchema, state: State, ctx: ExtensionContext): Promise<Mode | undefined>;
	function readJson(path: string, schema: typeof proposalSchema, state: State, ctx: ExtensionContext): Promise<Proposal | undefined>;
	async function readJson(
		path: string, schema: typeof modeSchema | typeof proposalSchema, state: State, ctx: ExtensionContext,
	): Promise<Mode | Proposal | undefined> {
		try {
			const parsed = schema.safeParse(JSON.parse(await readFile(path, "utf8")));
			if (parsed.success) return parsed.data;
			note(state, ctx, `Skill gate: ${path} is invalid; ignored.`);
		} catch (error) {
			const parsedError = fileErrorSchema.safeParse(error);
			if (!parsedError.success || parsedError.data.code !== "ENOENT") note(state, ctx, `Skill gate: ${path} is unreadable; ignored.`);
		}
	}
	const projectRoot = async (ctx: ExtensionContext) => await repoRoot(ctx.cwd) ?? ctx.cwd;
	async function dynaMode(ctx: ExtensionContext): Promise<boolean> {
		const state = stateFor(ctx);
		const project = await readJson(resolve(await projectRoot(ctx), ".omp/dyna-mode.json"), modeSchema, state, ctx);
		return (project ?? await readJson(machineModePath, modeSchema, state, ctx))?.enabled ?? false;
	}
	async function setDynaMode(ctx: ExtensionContext, enabled: boolean): Promise<string> {
		const root = await projectRoot(ctx);
		const path = resolve(root, ".omp/dyna-mode.json");
		await mkdir(dirname(path), { recursive: true });
		await writeFile(path, `${JSON.stringify({ schemaVersion: 1, enabled })}\n`);
		// Mode is local state: keep it out of commits without touching tracked ignore files.
		const gitDir = resolve(root, ".git");
		if ((await stat(gitDir).catch(() => null))?.isDirectory()) {
			const exclude = resolve(gitDir, "info/exclude");
			const current = await readFile(exclude, "utf8").catch(() => "");
			if (!current.split("\n").includes("/.omp/dyna-mode.json")) {
				await mkdir(dirname(exclude), { recursive: true });
				await appendFile(exclude, `${current && !current.endsWith("\n") ? "\n" : ""}/.omp/dyna-mode.json\n`);
			}
		}
		return path;
	}
	async function guardrailReason(targets: string[], ctx: ExtensionContext, state: State): Promise<string | undefined> {
		if (!targets.length || !(await dynaMode(ctx))) return;
		for (const path of targets) {
			const root = await repoRoot(path);
			if (!root) continue;
			const gate = await gateAt(root, state, ctx);
			if (gate?.fastGate && gate.pathCheck) continue;
			const rel = relative(root, path);
			const proposal = await readJson(resolve(root, ".omp/guardrail-proposal.json"), proposalSchema, state, ctx);
			const isGate = rel === ".omp/gate.json";
			if (!isGate && !proposal?.minimal.includes(rel) && !proposal?.full.includes(rel)) continue;
			const approved = state.guardrailApproval;
			if (approved && (isGate || proposal?.[approved].includes(rel))) continue;
			return approved
				? `Skill gate: owner approved the "${approved}" guardrail option, which does not list ${rel}.`
				: `Skill gate: ${rel} is a guardrail change; get owner sign-off via ask (question mentions "guardrail"; options "Minimal" and "Full") first.`;
		}
	}
	async function fingerprint(root: string): Promise<string | undefined> {
		try {
			const options = { cwd: root, timeout: 20000 };
			// v2 includes index blob IDs, so staged changes are covered without diffing against HEAD.
			// Diffing index vs worktree also works before the repository's first commit.
			const status = await pi.exec("git", ["status", "--porcelain=v2", "-z", "--untracked-files=all"], options);
			if (status.code !== 0 || status.killed) return;
			const diff = await pi.exec("git", ["diff", "--binary"], options);
			if (diff.code !== 0 || diff.killed) return;
			const hash = createHash("sha256").update(status.stdout).update("\0").update(diff.stdout);
			const entries = status.stdout.split("\0");
			let remaining = 16 * 1024 * 1024;
			let files = 0;
			for (let index = 0; index < entries.length; index++) {
				const entry = entries[index];
				if (entry.startsWith("2 ")) { index++; continue; } // rename's second NUL-delimited path
				if (!entry.startsWith("? ")) continue;
				if (++files > 1024) return;
				const path = resolve(root, entry.slice(2));
				const info = await lstat(path);
				if (info.size > remaining || (!info.isFile() && !info.isSymbolicLink())) return;
				remaining -= info.size;
				const content = info.isSymbolicLink() ? await readlink(path)
					: new Uint8Array(await Bun.file(path).slice(0, info.size).arrayBuffer());
				hash.update("\0").update(entry).update("\0").update(content);
			}
			return hash.digest("hex");
		} catch {
			// Fingerprinting is supplemental: Git/filesystem failures or bounded-read limits fail
			// open for this signal only; explicit edit, reference and pathCheck gates still apply.
			return;
		}
	}
	async function checkPaths(root: string, changed: string[], gate: Gate, state: State): Promise<string | undefined> {
		if (!gate.pathCheck) return;
		try {
			const result = await pi.exec(gate.pathCheck[0], [...gate.pathCheck.slice(1), ...changed], {
				cwd: root, timeout: gate.pathCheckTimeoutMs ?? 20000,
			});
			if (result.code !== 0 || result.killed) throw new Error(`${result.stdout}\n${result.stderr}`.trim());
			state.pathFailures.delete(root);
		} catch (error) {
			state.pathFailures.set(root, changed);
			return `Skill gate: pathCheck failed in ${root}: ${String(error)}`;
		}
	}
	function recordRead(event: ToolEvent, state: State): void {
		if (event.toolName !== "read" || event.isError || event.input.path === undefined) return;
		const url = event.input.path.replace(/:(?:raw|[\d,+-]+)(?::(?:raw|[\d,+-]+))*$/, "");
		if (url.startsWith("skill://")) state.reads.add(url);
	}
	async function bashStart(event: ToolEvent, ctx: ExtensionContext, state: State, live = true): Promise<void> {
		if (event.toolName !== "bash") return;
		const cwd = absolute(event.input.cwd ?? ctx.cwd, ctx.cwd);
		const root = cwd && await repoRoot(cwd);
		if (root) {
			const gate = await gateAt(root, state, ctx);
			const tree = live && ctx.agent.kind === "main" && gate?.fastGate?.join(" ") === event.input.command
				? await fingerprint(root) : undefined;
			state.bashStarts.set(event.toolCallId, { root, revision: state.revision, fingerprint: tree });
		}
	}
	async function recordResult(event: ToolEvent, ctx: ExtensionContext, state: State, runCheck: boolean) {
		recordRead(event, state);
		const details = event.details;
		if (event.toolName === "bash") {
			const started = state.bashStarts.get(event.toolCallId);
			state.bashStarts.delete(event.toolCallId);
			// Bash reports exitCode only for failures; a clean run has none. A backgrounded start
			// (`async`) or a timeout has not finished the gate, so neither counts as a run.
			const finished = details.async === undefined && details.timedOut !== true
				&& (event.isError === false || details.exitCode !== undefined);
			if (started && finished) {
				const gate = await gateAt(started.root, state, ctx);
				if (gate?.fastGate?.join(" ") === event.input.command) {
					state.passed.set(started.root, started.revision);
					if (runCheck && started.fingerprint !== undefined) state.checkedTrees.set(started.root, started.fingerprint);
				}
			}
			return;
		}
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
		const groups = new Map<string, string[]>();
		for (const path of changed) {
			const root = await repoRoot(path);
			if (!root) continue;
			if (matches(relative(root, path), map.codeGlobs)) state.edits.set(root, ++state.revision);
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
		state.baselines = previous?.baselines ?? new Map();
		state.checkedTrees = previous?.checkedTrees ?? new Map();
		state.pathFailures = previous?.pathFailures ?? new Map();
		state.authorFamilies = previous?.authorFamilies ?? new Set();
		states.set(ctx.sessionManager.getSessionId(), state);
		if (loadError) { note(state, ctx, loadError); return; }
		if (ctx.agent.kind === "main") {
			const root = await repoRoot(ctx.cwd);
			if (root && !state.baselines.has(root)) {
				const tree = await fingerprint(root);
				if (tree !== undefined) state.baselines.set(root, tree);
			}
		}
		const calls = new Map<string, ToolEvent>();
		for (const entry of ctx.sessionManager.getBranch()) {
			if (entry.type === "compaction") { state.reads.clear(); state.discovered = false; continue; }
			if (entry.type !== "message") continue;
			const message = entry.message;
			if (message.role === "assistant") for (const block of message.content) {
				if (block.type !== "toolCall") continue;
				const call = policyEvent({ type: "tool_call", toolName: block.name, toolCallId: block.id, input: block.arguments });
				calls.set(block.id, call);
				await bashStart(call, ctx, state, false);
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
		const author = ["task", "sonic", "designer"].includes(event.agent);
		if (!author && event.agent !== "verifier") return;
		const state = stateFor(ctx);
		// Core has already expanded agent frontmatter and configured roles into patterns.
		// Empty patterns inherit the parent model; retain every retry family's authorship.
		const models = event.patterns.length
			? event.patterns.map(pattern => ctx.models.resolve(pattern)).filter(model => model !== undefined)
			: [ctx.models.current()].filter(model => model !== undefined);
		if (!models.length) return { block: true, reason: `Skill gate: cannot resolve ${event.agent}'s model family.` };
		if (author) {
			for (const model of models) state.authorFamilies.add(ctx.models.family(model));
			return;
		}
		// Authors are always recorded so enabling dyna mode mid-session still sees them.
		if (!(await dynaMode(ctx))) return;
		const independent = models.filter(model => !state.authorFamilies.has(ctx.models.family(model)));
		if (!independent.length) return {
			block: true, reason: "Skill gate: verifier must use a different model family from every recorded author (task, sonic, designer).",
		};
		if (independent.length !== models.length) return {
			model: independent.map(model => `${model.provider}/${model.id}`),
			note: "Skill gate: removed verifier models from recorded author families.",
		};
	});
	pi.on("tool_call", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { block: true, reason: loadError };
		const state = stateFor(ctx);
		try {
			await bashStart(event, ctx, state);
			if (map.discoveryTools.includes(event.toolName) && !state.discovered) {
				const scopes = paths(event, map, ctx.cwd);
				if (!scopes.length && !event.hasScope) scopes.push(ctx.cwd);
				if ((await Promise.all(scopes.map(repoRoot))).some(Boolean)) {
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
					if (/^(?:git push\b|gh pr (?:create|merge)\b)/.test(action.command)) {
						if (!action.cwd) return {
							block: true, reason: "Skill gate: cannot resolve the command's effective cwd; use an explicit existing directory before git push or gh pr create/merge.",
						};
						const root = await repoRoot(action.cwd);
						const gate = root ? await gateAt(root, state, ctx, true) : undefined;
						if (gate?.agentMerge === "disabled") return {
							block: true, reason: "Skill gate: agentMerge is disabled; ask the owner before git push, gh pr create or gh pr merge in this repository.",
						};
						if (/^gh pr merge\b/.test(action.command) && gate?.sensitive?.length) {
							const args = action.ghArgs ?? [];
							const repoArgs: string[] = [];
							const mergeArgs: string[] = [];
							for (let index = 0; index < args.length; index++) {
								const arg = args[index];
								if (["-R", "--repo", "--hostname"].includes(arg)) {
									repoArgs.push(arg, args[++index]);
								} else if (/^(-R.|--repo=|--hostname=)/.test(arg)) repoArgs.push(arg);
								else mergeArgs.push(arg);
							}
							let selector: string | undefined;
							for (let index = 2; index < mergeArgs.length; index++) {
								const arg = mergeArgs[index];
								if (["--subject", "-t", "--body", "-b", "--body-file", "-F", "--author-email", "--match-head-commit"].includes(arg)) index++;
								else if (!arg.startsWith("-")) { selector = arg; break; }
							}
							const pull = await pi.exec("gh", [
								...repoArgs, "pr", "view", ...(selector ? [selector] : []),
								"--json", "number,url",
							], { cwd: action.cwd, timeout: 20000 });
							if (pull.code !== 0 || pull.killed) throw new Error(`cannot resolve PR: ${pull.stderr}`);
							const metadata = pullRequestSchema.parse(JSON.parse(pull.stdout));
							const url = new URL(metadata.url);
							const repository = /^\/([^/]+)\/([^/]+)\/pull\/(\d+)\/?$/.exec(url.pathname);
							if (!repository || Number(repository[3]) !== metadata.number) throw new Error("cannot resolve PR repository from URL");
							const files = await pi.exec("gh", [
								"api", "--hostname", url.host, "--paginate",
								`repos/${repository[1]}/${repository[2]}/pulls/${metadata.number}/files`,
								"--jq", ".[].filename",
							], { cwd: action.cwd, timeout: 20000 });
							if (files.code !== 0 || files.killed) throw new Error(`cannot resolve PR files: ${files.stderr}`);
							if (files.stdout.split("\n").filter(Boolean).some(path =>
								gate.sensitive!.some(pattern => pattern.endsWith("/")
									? `/${path}`.includes(`/${pattern}`)
									: basename(path).startsWith(pattern)))) return {
								block: true, reason: "Skill gate: owner must merge PRs touching sensitive paths.",
							};
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
				if (state.revision === 0 && matches(rel, map.codeGlobs)) triggers.push("firstEdit");
				if (!(await access(path).then(() => true, () => false))) triggers.push("newFile");
				if (matches(rel, gate?.testGlobs ?? map.testGlobs)) triggers.push("test");
				for (const url of missing(state, map, triggers)) required.add(url);
			}
			if (required.size) return { block: true, reason: readReason([...required]) };
		} catch (error) { return { block: true, reason: `Skill gate: policy could not be evaluated: ${String(error)}` }; }
	});
	pi.on("tool_result", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { additionalContext: loadError };
		try { return await recordResult(event, ctx, stateFor(ctx), true); }
		catch (error) { return { additionalContext: `Skill gate: post-edit policy failed: ${String(error)}` }; }
	});
	pi.on("session_stop", async (_event, ctx) => {
		if (ctx.agent.kind !== "main") return;
		if (loadError) return { decision: "block", reason: loadError };
		const state = stateFor(ctx);
		try {
			const dyna = await dynaMode(ctx);
			const reasons: string[] = [];
			let changedTree = false;
			const roots = new Set([...state.edits.keys(), ...state.pathFailures.keys(), ...state.baselines.keys()]);
			for (const root of roots) {
				const gate = await gateAt(root, state, ctx);
				const failedPaths = state.pathFailures.get(root);
				if (failedPaths && gate?.pathCheck) {
					const existing: string[] = [];
					for (const path of failedPaths) {
						try { await lstat(path); existing.push(path); }
						catch (error) {
							const parsed = fileErrorSchema.safeParse(error);
							if (!parsed.success || parsed.data.code !== "ENOENT") throw error;
						}
					}
					const error = await checkPaths(root, existing, gate, state);
					if (error) reasons.push(error);
				}
				// Outside dyna mode the fast gate is not required and tree drift (pulls, delegated
				// edits) does not trigger handoff requirements; this session's own edits still do.
				if (!dyna) continue;
				const tree = gate?.fastGate && state.baselines.has(root) ? await fingerprint(root) : undefined;
				const uncheckedTree = tree !== undefined && tree !== state.baselines.get(root) && tree !== state.checkedTrees.get(root);
				changedTree ||= uncheckedTree;
				if (gate?.fastGate && (uncheckedTree || (state.passed.get(root) ?? -1) < (state.edits.get(root) ?? -1))) {
					reasons.push(`run ${gate.fastGate.join(" ")} in ${root} after the last edit (any exit code)`);
				}
			}
			const urls = state.edits.size || changedTree ? missing(state, map, ["verification"]) : [];
			if (urls.length) reasons.push(`read ${urls.join(" and ")}`);
			if (reasons.length) {
				return { decision: "block", reason: `Skill gate: ${reasons.join("; ")}, then retry handoff.` };
			}
		} catch (error) { return { decision: "block", reason: `Skill gate: handoff policy failed: ${String(error)}` }; }
	});
	pi.on("before_agent_start", async (event, ctx) => {
		if (loadError || ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const state = stateFor(ctx);
		const keyword = /(?:^|\s)\$dynaflows(?:\s+(on|off))?(?=\s|$)/i.exec(event.prompt ?? "");
		if (keyword) {
			const enabled = keyword[1]?.toLowerCase() !== "off";
			const path = await setDynaMode(ctx, enabled);
			ctx.ui.notify(`Skill gate: dyna mode ${enabled ? "on" : "off"} (${path}).`, "info");
			state.pointed = false;
			if (!enabled) return { message: {
				customType: "skill-gate-workflow", content: "Dyna mode off: do not apply omp-dyna-workflows.",
				display: false, attribution: "agent",
			} };
		}
		if (state.pointed || !(await dynaMode(ctx))) return;
		state.pointed = true;
		const lines = [`Dyna mode on. Route this work with ${map.references.workflow.join(" and ")}.`];
		const root = await repoRoot(ctx.cwd);
		const gate = root ? await gateAt(root, state, ctx) : undefined;
		if (root && !(gate?.fastGate && gate.pathCheck)) lines.push(
			`Guardrails missing in ${root}: .omp/gate.json lacks fastGate or pathCheck.`,
			"Before guardrail work, ask the owner whether to analyse and propose guardrails.",
			"If yes: write .omp/guardrail-proposal.json {\"schemaVersion\":1,\"minimal\":[repo-relative paths],\"full\":[repo-relative paths]};",
			"minimal = least needed for fastGate and pathCheck so one gate run clears handoff; full = every recommended guardrail.",
			"Present both, then call ask with a question containing \"guardrail\" and options \"Minimal\" and \"Full\".",
			"Writes to .omp/gate.json and proposal paths stay blocked until the owner picks one.",
		);
		return { message: {
			customType: "skill-gate-workflow", content: lines.join(" "), display: false, attribution: "agent",
		} };
	});
	pi.registerCommand("extension-health-skill-gate", {
		description: "Skill gate registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Skill gate registered", loadError ? "error" : "info"); },
	});
}
