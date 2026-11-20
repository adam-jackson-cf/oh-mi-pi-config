import { access, realpath, stat } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, relative, resolve } from "node:path";
import { z, type ExtensionContext, type ToolCallEvent, type ToolResultEvent } from "@oh-my-pi/pi-coding-agent";
import { findRecord, loadGate, type Gate, type Mode } from "./repo-record.ts";

// The one hang guard for the rails' own short git/gh reads and the harness path check.
const SUBPROCESS_TIMEOUT_MS = 20_000;
const stringList = z.array(z.string());
const regexpSource = z.string().refine(value => {
	try { return RegExp(value).source.length > 0; } catch { return false; }
});
function referenceSet(mode: Mode, keys: string[]) {
	return z.record(z.string(), stringList).superRefine((references, ctx) => {
		for (const key of new Set([...keys, ...Object.keys(references)])) {
			const urls = references[key];
			if (!keys.includes(key) || !urls?.length || urls.some(url => !url.startsWith("skill://"))) {
				ctx.addIssue({ code: "custom", message: `invalid references.${mode}.${key}` });
			}
		}
	});
}
const mappingSchema = z.object({
	references: z.object({
		minimal: referenceSet("minimal", ["git"]),
		structured: referenceSet("structured", ["firstEdit", "newFile", "testFile", "verification", "workflow", "git"]),
	}).strict(),
	testGlobs: stringList,
	structuredRules: stringList,
	codeGlobs: stringList,
	editTools: stringList,
	gitCommand: regexpSource,
	pathFields: z.record(z.string(), stringList),
	resolveDevice: z.string(),
	rejectDevice: z.string(),
});
const pathValueSchema = z.union([z.string(), stringList]).optional().catch(undefined);
const inputFieldsSchema = z.record(z.string(), pathValueSchema).catch({});
const inputSchema = z.object({
	path: z.string().optional().catch(undefined),
	command: z.string().optional().catch(undefined),
	cwd: z.string().optional().catch(undefined),
	input: z.string().optional().catch(undefined),
	// The write tool's whole new file text.
	content: z.string().optional().catch(undefined),
}).catch({ path: undefined, command: undefined, cwd: undefined, input: undefined, content: undefined });
const detailsSchema = z.object({
	applied: z.boolean().optional().catch(undefined),
	files: stringList.optional().catch(undefined),
}).catch({ applied: undefined, files: undefined });
const fileErrorSchema = z.object({ code: z.string().optional() });
type Mapping = z.infer<typeof mappingSchema>;
type RepoRecord = { dir: string; gate: Gate };
type GitAction = {
	command: string; args: string[]; cwd: string | undefined; config: string[];
	// A hook-skip variable the command assigns, exports or unsets anywhere (see `hookSkipVariables`).
	hookSkip?: string;
};
interface State {
	reads: Set<string>;
	notes: Set<string>;
	proposal: string[];
}
type ToolEvent = {
	toolName: string;
	toolCallId: string;
	input: z.infer<typeof inputSchema>;
	pathFields: z.infer<typeof inputFieldsSchema>;
	isError: boolean | undefined;
	details: z.infer<typeof detailsSchema>;
};

function policyEvent(event: ToolCallEvent | ToolResultEvent): ToolEvent {
	return {
		toolName: event.toolName, toolCallId: event.toolCallId,
		input: inputSchema.parse(event.input), pathFields: inputFieldsSchema.parse(event.input),
		isError: event.type === "tool_result" ? event.isError : undefined,
		details: detailsSchema.parse(event.type === "tool_result" ? event.details : undefined),
	};
}
function absolute(path: string, cwd: string): string | undefined {
	if (/^[a-z][\w+.-]*:\/\//i.test(path)) return;
	return resolve(cwd, path.startsWith("~/") ? resolve(homedir(), path.slice(2)) : path);
}
// Native realpath preserves the filesystem's canonical spelling (including on case-insensitive
// volumes). New worktree directories need not exist yet: retain their missing suffix.
function canonicalPath(path: string): string {
	const suffix: string[] = [];
	let ancestor = path;
	for (;;) {
		try { return resolve(realpathSync.native(ancestor), ...suffix); }
		catch (error) {
			const parsed = fileErrorSchema.safeParse(error);
			if (!parsed.success || parsed.data.code !== "ENOENT") throw error;
			const parent = dirname(ancestor);
			if (parent === ancestor) throw error;
			suffix.unshift(basename(ancestor));
			ancestor = parent;
		}
	}
}
function worktreeTarget(args: string[]): string | undefined {
	for (let i = 2; i < args.length; i++) {
		const arg = args[i] ?? "";
		if (arg === "--") return args[i + 1];
		if (["-b", "-B", "--reason"].includes(arg)) { i++; continue; }
		if (!arg.startsWith("-")) return arg;
	}
	return undefined;
}
function absentFile(error: NodeJS.ErrnoException): null {
	const parsed = fileErrorSchema.safeParse(error);
	if (parsed.success && parsed.data.code === "ENOENT") return null;
	throw error;
}
async function repoRoot(path: string): Promise<string | undefined> {
	let dir = (await stat(path).catch(absentFile))?.isDirectory() ? path : dirname(path);
	for (;;) {
		if (await access(resolve(dir, ".git")).then(() => true).catch(absentFile)) return dir;
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
async function realTarget(lexical: string): Promise<string> {
	let parent = lexical;
	for (;;) {
		try {
			return resolve(await realpath(parent), relative(parent, lexical));
		} catch (error) {
			const parsed = fileErrorSchema.safeParse(error);
			if (!parsed.success || parsed.data.code !== "ENOENT" || dirname(parent) === parent) throw error;
			parent = dirname(parent);
		}
	}
}
async function rootEscapeReason(path: string, enclosingRoot: string | undefined, resolved: string): Promise<string | undefined> {
	let root = enclosingRoot;
	while (root) {
		const outside = relative(await realpath(root), resolved);
		if (outside === ".." || outside.startsWith("../") || isAbsolute(outside)) {
			return `Agent rails: subagent write path "${path}" resolves to "${resolved}", outside git root "${root}"; use an absolute path inside your assigned worktree without a symlink escape.`;
		}
		const above = dirname(root);
		root = above === root ? undefined : await repoRoot(above);
	}
	return undefined;
}
async function subagentTargetReason(target: string): Promise<string | undefined> {
	const path = target.startsWith("~/") ? resolve(homedir(), target.slice(2)) : target;
	if (/^[a-z][\w+.-]*:\/\//i.test(path)) return undefined;
	if (!isAbsolute(path)) return `Agent rails: subagent write path "${path}" is relative; use an absolute path inside your assigned worktree.`;
	try {
		const lexical = resolve(path);
		const root = await repoRoot(lexical);
		return await rootEscapeReason(path, root, await realTarget(lexical));
	} catch (error) {
		return `Agent rails: cannot evaluate subagent write path "${path}": ${String(error)}`;
	}
}
async function subagentPathReason(targets: string[]): Promise<string | undefined> {
	for (const target of targets) {
		const reason = await subagentTargetReason(target);
		if (reason) return reason;
	}
	return undefined;
}
function recordRead(event: ToolEvent, state: State): void {
	if (event.toolName !== "read" || event.isError || event.input.path === undefined) return;
	const url = event.input.path.replace(/:(?:raw|[\d,+-]+)(?::(?:raw|[\d,+-]+))*$/, "");
	if (url.startsWith("skill://")) state.reads.add(url);
}
function matches(path: string, globs: string[]): boolean {
	return globs.some(glob => new Bun.Glob(glob).match(path));
}
function missing(state: State, map: Mapping, mode: Mode, triggers: string[]): string[] {
	const references: Record<string, string[]> = map.references[mode];
	return [...new Set(triggers.flatMap(trigger => references[trigger] ?? []))]
		.filter(url => !state.reads.has(url));
}
// Ends `text` with a full stop unless it already ends a sentence.
function sentence(text: string): string {
	return /[.!?]$/.test(text) ? text : `${text}.`;
}
function readReason(urls: string[], mode: Mode): string {
	return `Agent rails (${mode} mode): read ${urls.join(" and ")}, then retry.`;
}

const WRAPPER_OPTIONS = new Map<string, string[]>(Object.entries({
	sudo: ["-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "--close-from", "-D", "--chdir", "-T", "--command-timeout", "-R", "--chroot"],
	env: ["-u", "--unset", "-C", "--chdir", "-S", "--split-string"],
	time: ["-f", "--format", "-o", "--output"],
	command: [], nohup: [], nice: ["-n", "--adjustment"], exec: ["-a"],
	xargs: ["-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-J", "-L", "--max-lines", "-n", "--max-args", "-P", "--max-procs", "-s", "--max-chars"],
	timeout: ["-k", "--kill-after", "-s", "--signal"],
}));

type ToolLauncher = { subcommands: string[]; valued: string[]; separator?: boolean };
const TOOL_LAUNCHERS = new Map<string, ToolLauncher>(Object.entries({
	mise: { subcommands: ["exec", "x"], valued: ["-C", "--cd", "-E", "--env", "-j", "--jobs"], separator: true },
	pnpm: { subcommands: ["exec", "dlx"], valued: ["--package", "-p"] },
	npx: { subcommands: [], valued: ["--package", "-p"] },
	bunx: { subcommands: [], valued: ["--package", "-p"] },
	uv: { subcommands: ["run"], valued: ["--python", "--project", "--directory", "--with", "--with-editable", "--with-requirements", "--extra", "--group"] },
	poetry: { subcommands: ["run"], valued: ["--directory", "-C", "--project", "-P"] },
}));

const COMMAND_PREFIXES = ["then", "do", "else", "{", "!", "if", "while", "until"];
const BLOCK_STARTS = ["then", "do", "else", "{", "!"];
const VARIABLE_COMMANDS = ["export", "unset", "declare", "typeset", "readonly", "local"];
const GIT_VALUED_OPTIONS = ["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"];
const SHELL_NAME = /^[A-Za-z_]\w*$/;
const SEPARATOR_TOKEN = /^(?:&&|\|\||[;&|()\n])$/;
const SUBSTITUTION_MARK = /\0(\d+)\0/g;
const QUOTED_TEXT = /"((?:\\.|[^"\\])*)"|'([^']*)'|\\(.)/g;

const isGitWord = (word: string) => ["git", "gh"].includes(basename(word));

function unquote(text: string): string {
	return text.replace(QUOTED_TEXT, (_match, double: string | undefined, single: string | undefined, escaped: string | undefined) =>
		double?.replace(/\\(["\\$`])/g, "$1") ?? single ?? escaped ?? "");
}

interface NestedOpening {
	start: number;
	closing: string;
}

interface Substitution {
	script: string;
	end: number;
}

interface MaskedCommand {
	shell: string;
	substitutions: string[];
}

function quoteOpening(char: string, closing: string): string | undefined {
	if (char === "`") return closing === "`" ? undefined : char;
	if (char === "'" || char === '"') return closing === '"' ? undefined : char;
	return undefined;
}

function nestedOpening(command: string, index: number, closing: string): NestedOpening | undefined {
	const char = command[index] ?? "";
	const quote = quoteOpening(char, closing);
	if (quote) return { start: index + 1, closing: quote };
	if (char === "$" && command[index + 1] === "(") return { start: index + 2, closing: ")" };
	if (char === "(" && closing === ")") return { start: index + 1, closing: ")" };
	return undefined;
}

function closingIndex(command: string, start: number, closing: string, nesting = 0): number {
	if (nesting > 16) throw new Error("shell scan exceeds supported bounds");
	for (let i = start; i < command.length; i++) {
		const char = command[i];
		if (char === "\\" && closing !== "'") { i++; continue; }
		if (char === closing) return i;
		if (closing === "'") continue;
		const nested = nestedOpening(command, i, closing);
		if (nested) i = closingIndex(command, nested.start, nested.closing, nesting + 1);
	}
	throw new Error("unterminated shell quote or command substitution; cannot parse safely");
}

function substitutionAt(command: string, index: number): Substitution {
	const backtick = command[index] === "`";
	const start = index + (backtick ? 1 : 2);
	const end = closingIndex(command, start, backtick ? "`" : ")");
	const script = command.slice(start, end);
	return { script: backtick ? script.replace(/\\([$`\\])/g, "$1") : script, end };
}

function maskSubstitutions(command: string): MaskedCommand {
	const substitutions: string[] = [];
	let shell = "";
	let quoted = false;
	for (let i = 0; i < command.length; i++) {
		const char = command[i] ?? "";
		if (char === "\\") { shell += command.slice(i, i + 2); i++; }
		else if (char === "'" && !quoted) {
			const end = closingIndex(command, i + 1, "'");
			shell += command.slice(i, end + 1); i = end;
		} else if (char === '"') { quoted = !quoted; shell += char; }
		else if (char === "`" || (char === "$" && command[i + 1] === "(")) {
			const { script, end } = substitutionAt(command, i);
			shell += `\0${substitutions.length}\0`;
			substitutions.push(script);
			i = end;
		} else shell += char;
	}
	return { shell, substitutions };
}

function hasGitWord(body: string, substitutions: string[]): boolean {
	const source = body.replace(SUBSTITUTION_MARK, (_match, index: string) => substitutions[Number(index)] ?? "");
	return /\b(?:git|gh)\b/.test(source) || /\b(?:git|gh)\b/.test(unquote(source));
}

async function directory(path: string | undefined, from: string | undefined): Promise<string | undefined> {
	if (!path || path === "-" || /[$`*?[\]]/.test(path)) return undefined;
	const target = isAbsolute(path) || path.startsWith("~/") ? absolute(path, "/") : from && resolve(from, path);
	return target && (await stat(target).catch(() => null))?.isDirectory() ? target : undefined;
}

function skipHeader(tokens: string[], start: number): number {
	let body = start;
	if (tokens[body] === "(" && tokens[body + 1] === ")") body += 2;
	while (tokens[body] === "\n") body++;
	return body;
}

function closingBrace(tokens: string[], body: number): number {
	let braces = 1;
	let end = body + 1;
	for (; end < tokens.length && braces; end++) {
		if (tokens[end] === "{") braces++;
		if (tokens[end] === "}") braces--;
	}
	if (braces) throw new Error("unterminated shell function definition; cannot parse safely");
	return end;
}

type ScanState = { hookSkip?: string };

function separatesWrappedCommand(launcher: string, tool: ToolLauncher, words: string[]): boolean {
	if (tool.separator) return true;
	const separator = words.indexOf("--");
	const git = words.findIndex(isGitWord);
	return ["uv", "poetry"].includes(launcher) && separator >= 0 && (git < 0 || separator < git);
}

function changesDirectory(launcher: string, name: string): boolean {
	return (launcher === "env" && ["-C", "--chdir"].includes(name)) || (launcher === "sudo" && ["-D", "--chdir"].includes(name));
}

function markHookSkip(actions: GitAction[], hookSkip: string | undefined, cwd: string | undefined): GitAction[] {
	if (!hookSkip) return actions;
	if (!actions.length) actions.push({ command: "hook environment", args: [], cwd, config: [], hookSkip });
	for (const action of actions) action.hookSkip ??= hookSkip;
	return actions;
}

class ShellScan {
	readonly actions: GitAction[] = [];
	readonly subshells: (string | undefined)[] = [];
	readonly substitutions: string[];
	readonly scan: ScanState;
	readonly depth: number;
	cwd: string | undefined;
	words: string[] = [];

	constructor(substitutions: string[], scan: ScanState, depth: number, cwd: string | undefined) {
		this.substitutions = substitutions;
		this.scan = scan;
		this.depth = depth;
		this.cwd = cwd;
	}

	touch(word: string | undefined): void {
		const name = word?.split("=")[0] ?? "";
		if (word !== undefined && Object.hasOwn(hookSkipVariables, name)) this.scan.hookSkip ??= word;
	}

	async nested(command: string): Promise<void> {
		this.actions.push(...await gitActions(command, this.cwd, this.scan, this.depth + 1));
	}

	async read(tokens: string[]): Promise<void> {
		for (let index = 0; index < tokens.length; index++) {
			const body = this.functionBodyStart(tokens, index);
			const end = body === index ? undefined : await this.functionDefinition(tokens, body);
			if (end === undefined) await this.token(tokens[index] ?? "");
			else index = end - 1;
		}
		await this.flush();
	}

	functionBodyStart(tokens: string[], index: number): number {
		const token = tokens[index] ?? "";
		if (!this.words.every(word => BLOCK_STARTS.includes(word))) return index;
		if (token === "function" && SHELL_NAME.test(tokens[index + 1] ?? "")) return skipHeader(tokens, index + 2);
		if (SHELL_NAME.test(token) && tokens[index + 1] === "(" && tokens[index + 2] === ")") return skipHeader(tokens, index + 1);
		return index;
	}

	async functionDefinition(tokens: string[], body: number): Promise<number | undefined> {
		if (tokens[body] !== "{") return undefined;
		const end = closingBrace(tokens, body);
		if (hasGitWord(tokens.slice(body + 1, end - 1).join(" "), this.substitutions)) {
			throw new Error("shell function definition contains a git/gh word; persistent shell functions cannot be parsed safely");
		}
		await this.flush();
		return end;
	}

	async token(token: string): Promise<void> {
		for (const match of token.matchAll(SUBSTITUTION_MARK)) await this.nested(this.substitutions[Number(match[1])] ?? "");
		if (!SEPARATOR_TOKEN.test(token)) {
			this.words.push(unquote(token.replace(SUBSTITUTION_MARK, (_match, index: string) => `$(${this.substitutions[Number(index)] ?? ""})`)));
			return;
		}
		await this.flush();
		if (token === "(") this.subshells.push(this.cwd);
		if (token === ")") this.cwd = this.subshells.pop();
	}

	async flush(): Promise<void> {
		while (this.words.length) {
			this.skipCommandPrefixes();
			const launcher = basename(this.words[0] ?? "");
			const tool = TOOL_LAUNCHERS.get(launcher);
			if (tool && (!tool.subcommands.length || tool.subcommands.includes(this.words[1] ?? ""))) {
				await this.unwrapTool(launcher, tool);
				continue;
			}
			const valued = WRAPPER_OPTIONS.get(launcher);
			if (!valued) break;
			if (await this.unwrapWrapper(launcher, valued)) return;
		}
		await this.dispatch();
		this.words = [];
	}

	skipCommandPrefixes(): void {
		while (/^[A-Za-z_]\w*=/.test(this.words[0] ?? "") || COMMAND_PREFIXES.includes(this.words[0] ?? "")) this.touch(this.words.shift());
	}

	async unwrapTool(launcher: string, tool: ToolLauncher): Promise<void> {
		this.words.shift();
		if (tool.subcommands.length) this.words.shift();
		if (tool.separator && !this.words.includes("--")) {
			throw new Error(`${launcher} exec/x requires -- before the wrapped command; cannot parse safely`);
		}
		await this.skipToolOptions(launcher, tool, separatesWrappedCommand(launcher, tool, this.words));
		if (!isGitWord(this.words[0] ?? "") && this.words.some(isGitWord)) {
			throw new Error(`${launcher} wrapped command could not be parsed safely; put git first after options or use --`);
		}
	}

	async skipToolOptions(launcher: string, tool: ToolLauncher, explicitSeparator: boolean): Promise<void> {
		while (this.words.length > 0) {
			const option = this.words[0] ?? "";
			if (!explicitSeparator && !option.startsWith("-")) return;
			this.words.shift();
			if (option === "--") return;
			const value = this.toolOptionValue(option, tool.valued);
			if (launcher === "mise" && ["-C", "--cd"].includes(option.split("=")[0] ?? "")) this.cwd = await directory(value, this.cwd);
		}
	}

	toolOptionValue(option: string, valued: string[]): string | undefined {
		if (!valued.includes(option.split("=")[0] ?? "")) return undefined;
		return option.includes("=") ? option.slice(option.indexOf("=") + 1) : this.words.shift();
	}

	wrapperOptionValue(option: string, valued: string[]): string | undefined {
		if (valued.includes(option)) return this.words.shift();
		return option.includes("=") ? option.slice(option.indexOf("=") + 1) : undefined;
	}

	async unwrapWrapper(launcher: string, valued: string[]): Promise<boolean> {
		this.words.shift();
		while (this.words[0]?.startsWith("-")) {
			const option = this.words.shift() ?? "";
			if (option === "--") break;
			if (launcher === "command" && ["-v", "-V"].includes(option)) { this.words = []; return true; }
			if (await this.wrapperOption(launcher, option, this.wrapperOptionValue(option, valued))) return true;
		}
		if (launcher === "timeout") this.words.shift();
		return false;
	}

	async wrapperOption(launcher: string, option: string, value: string | undefined): Promise<boolean> {
		const name = option.split("=")[0] ?? "";
		if (launcher === "env" && ["-S", "--split-string"].includes(name)) {
			if (value) await this.nested([value, ...this.words.map(word => JSON.stringify(word))].join(" "));
			this.words = [];
			return true;
		}
		if (changesDirectory(launcher, name)) this.cwd = await directory(value, this.cwd);
		if (launcher === "env" && ["-u", "--unset"].includes(name)) this.touch(value);
		else if (launcher === "env" && /^-u./.test(option)) this.touch(option.slice(2));
		return false;
	}

	async dispatch(): Promise<void> {
		const executable = basename(this.words.shift() ?? "");
		if (executable === "alias") this.refuseGitAlias();
		if (VARIABLE_COMMANDS.includes(executable)) this.words.forEach(word => this.touch(word));
		else if (executable === "eval") await this.nested(this.words.join(" "));
		else if (["bash", "sh", "zsh"].includes(executable)) await this.shellScript();
		else if (["cd", "pushd", "popd"].includes(executable)) await this.changeDirectory(executable);
		else if (executable === "git") await this.git();
		else if (executable === "gh") this.gh();
	}

	refuseGitAlias(): void {
		if (this.words.some(word => word.includes("=") && hasGitWord(word.slice(word.indexOf("=") + 1), this.substitutions))) {
			throw new Error("alias definition contains a git/gh word; persistent shell aliases cannot be parsed safely");
		}
	}

	async shellScript(): Promise<void> {
		while (this.words[0]?.startsWith("-")) {
			const option = this.words.shift() ?? "";
			if (/^-[^-]*c/.test(option)) {
				if (this.words[0]) await this.nested(this.words[0]);
				return;
			}
			if (["-o", "+o", "-O"].includes(option)) this.words.shift();
		}
	}

	async changeDirectory(executable: string): Promise<void> {
		while (["-L", "-P", "--"].includes(this.words[0] ?? "")) this.words.shift();
		this.cwd = executable === "popd" ? undefined : await directory(this.words.length === 1 ? this.words[0] : undefined, this.cwd);
	}

	async git(): Promise<void> {
		let effective = this.cwd;
		const config: string[] = [];
		while (this.words[0]?.startsWith("-")) effective = await this.gitOption(this.words.shift() ?? "", effective, config);
		this.actions.push({ command: `git ${this.words.join(" ")}`, args: [...this.words], cwd: effective, config });
	}

	async gitOption(option: string, effective: string | undefined, config: string[]): Promise<string | undefined> {
		if (GIT_VALUED_OPTIONS.includes(option)) return this.gitValuedOption(option, effective, config);
		if (option.startsWith("--config-env=")) config.push(option.slice("--config-env=".length));
		else if (option.startsWith("-c") && option.length > 2) config.push(option.slice(2));
		else if (option.startsWith("-C") && option.length > 2) return directory(option.slice(2), effective);
		return effective;
	}

	async gitValuedOption(option: string, effective: string | undefined, config: string[]): Promise<string | undefined> {
		const value = this.words.shift();
		if ((option === "-c" || option === "--config-env") && value) config.push(value);
		return option === "-C" ? directory(value, effective) : effective;
	}

	gh(): void {
		if (this.words.some(word => ["-R", "--repo", "--hostname"].includes(word) || /^(-R.|--repo=|--hostname=)/.test(word))) {
			throw new Error("gh remote repository overrides are unsupported; use the validated local target repository");
		}
		this.actions.push({ command: `gh ${this.words.join(" ")}`, args: [...this.words], cwd: this.cwd, config: [] });
	}
}

async function gitActions(command: string, cwd: string | undefined, scan: ScanState = {}, depth = 0): Promise<GitAction[]> {
	if (depth > 16 || command.length > 131072) throw new Error("shell scan exceeds supported bounds");
	if (command.includes("\0")) throw new Error("shell scan does not support NUL bytes");
	const { shell, substitutions } = maskSubstitutions(command);
	const tokens = shell.match(/(?:[^\s;&|()"'\\]+|\\.|"(?:\\.|[^"\\])*"|'[^']*')+|&&|\|\||[;&|()\n]/g) ?? [];
	const reader = new ShellScan(substitutions, scan, depth, cwd);
	await reader.read(tokens);
	return markHookSkip(reader.actions, scan.hookSkip, reader.cwd);
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
const VALUE_SHORTS = new Map<string, string[]>([
	["commit", Object.keys(commitValueShorts)],
	["merge", ["m", "F", "s", "X"]],
	["rebase", ["x", "X", "C", "s"]],
	["cherry-pick", ["m", "X"]],
	["revert", ["m", "X"]],
]);
const VALUE_LONGS = new Map<string, string[]>([
	["commit", Object.keys(commitValueLongs)],
	["merge", ["--message", "--file", "--strategy", "--strategy-option", "--into-name"]],
	["rebase", ["--exec", "--onto", "--strategy", "--strategy-option", "--whitespace"]],
	["cherry-pick", ["--mainline", "--strategy", "--strategy-option"]],
	["revert", ["--mainline", "--strategy", "--strategy-option"]],
	["am", ["--patch-format", "--directory", "--exclude", "--include", "--whitespace"]],
]);
type OptionStep = { bypass: boolean; skipValue: boolean };
const PLAIN_ARGUMENT: OptionStep = { bypass: false, skipValue: false };

function longOptionStep(arg: string, valueLongs: string[]): OptionStep {
	const name = arg.split("=")[0] ?? "";
	return {
		bypass: name.length > "--no-".length && "--no-verify".startsWith(name),
		skipValue: !arg.includes("=") && name.length > 2 && valueLongs.some(long => long.startsWith(name)),
	};
}

function shortOptionStep(arg: string, operation: string, valueShorts: string[]): OptionStep {
	if (!arg.startsWith("-") || arg === "-") return PLAIN_ARGUMENT;
	for (let at = 1; at < arg.length; at++) {
		const flag = arg[at] ?? "";
		if (flag === "n" && operation === "commit") return { bypass: true, skipValue: false };
		if (flag === "u" || flag === "S") break;
		if (valueShorts.includes(flag)) return { bypass: false, skipValue: at === arg.length - 1 };
	}
	return PLAIN_ARGUMENT;
}

function hooksPathConfig(config: string[]): string | undefined {
	return config.find(entry => (entry.split("=")[0] ?? "").toLowerCase() === "core.hookspath");
}

function bypassArgument(args: string[], operation: string): string | undefined {
	const valueShorts = VALUE_SHORTS.get(operation) ?? [];
	const valueLongs = VALUE_LONGS.get(operation) ?? [];
	for (let index = 0; index < args.length; index++) {
		const arg = args[index] ?? "";
		if (arg === "--") break;
		const step = arg.startsWith("--") ? longOptionStep(arg, valueLongs) : shortOptionStep(arg, operation, valueShorts);
		if (step.bypass) return arg;
		if (step.skipValue) index++;
	}
	return undefined;
}

function commitBypass(action: GitAction): string | undefined {
	return bypassArgument(action.args.slice(1), action.args[0] ?? "") ?? hooksPathConfig(action.config) ?? action.hookSkip;
}

async function readRecord(root: string, configRoot: string): Promise<RepoRecord | undefined> {
	const dir = await findRecord(root, configRoot);
	if (dir) return { dir, gate: await loadGate(dir) };
	return undefined;
}
function note(state: State, ctx: ExtensionContext, message: string): void {
	if (state.notes.has(message)) return;
	state.notes.add(message);
	ctx.ui.notify(message, "info");
}
export { recordRead, SUBPROCESS_TIMEOUT_MS, stringList, mappingSchema, detailsSchema, policyEvent, absolute, canonicalPath, worktreeTarget, absentFile, repoRoot, pathArguments, paths, subagentPathReason, matches, missing, sentence, readReason, gitActions, commitBypass, readRecord, note };
export { fileErrorSchema };
export type { Mapping, RepoRecord, GitAction, State, ToolEvent };
