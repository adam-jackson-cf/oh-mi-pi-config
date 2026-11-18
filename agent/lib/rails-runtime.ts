import { access, realpath, stat } from "node:fs/promises";
import { realpathSync } from "node:fs";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, relative, resolve } from "node:path";
import { z, type ExtensionContext, type ToolCallEvent, type ToolResultEvent } from "@oh-my-pi/pi-coding-agent";
import { findRecord, loadGate, type Gate } from "./repo-record.ts";

// The one hang guard for the rails' own short git/gh reads and the harness path check.
const SUBPROCESS_TIMEOUT_MS = 20_000;
const stringList = z.array(z.string());
const regexpSource = z.string().refine(value => {
	try { new RegExp(value); return true; } catch { return false; }
});
const mappingSchema = z.object({
	references: z.record(z.string(), stringList).superRefine((references, ctx) => {
		for (const key of ["firstEdit", "newFile", "test", "verification", "workflow", "git"]) {
			const urls = references[key];
			if (!urls?.length || urls.some(url => !url.startsWith("skill://"))) {
				ctx.addIssue({ code: "custom", message: `invalid references.${key}` });
			}
		}
	}),
	testGlobs: stringList,
	dynaflowsRules: stringList,
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
	command: string; args: string[]; cwd?: string; config: string[];
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
	isError?: boolean;
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
function fresh(): State {
	return { reads: new Set(), notes: new Set(), proposal: [] };
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
		const arg = args[i];
		if (arg === "--") return args[i + 1];
		if (["-b", "-B", "--reason"].includes(arg)) { i++; continue; }
		if (!arg.startsWith("-")) return arg;
	}
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
async function subagentPathReason(targets: string[]): Promise<string | undefined> {
	for (const target of targets) {
		const path = target.startsWith("~/") ? resolve(homedir(), target.slice(2)) : target;
		if (/^[a-z][\w+.-]*:\/\//i.test(path)) continue;
		if (!isAbsolute(path)) return `Agent rails: subagent write path "${path}" is relative; use an absolute path inside your assigned worktree.`;
		try {
			const lexical = resolve(path);
			let root = await repoRoot(lexical);
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
		} catch (error) {
			return `Agent rails: cannot evaluate subagent write path "${path}": ${String(error)}`;
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
// Ends `text` with a full stop unless it already ends a sentence.
function sentence(text: string): string {
	return /[.!?]$/.test(text) ? text : `${text}.`;
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

type ToolLauncher = { subcommands: string[]; valued: string[]; separator?: boolean };
const toolLaunchers = {
	mise: { subcommands: ["exec", "x"], valued: ["-C", "--cd", "-E", "--env", "-j", "--jobs"], separator: true },
	pnpm: { subcommands: ["exec", "dlx"], valued: ["--package", "-p"] },
	npx: { subcommands: [], valued: ["--package", "-p"] },
	bunx: { subcommands: [], valued: ["--package", "-p"] },
	uv: { subcommands: ["run"], valued: ["--python", "--project", "--directory", "--with", "--with-editable", "--with-requirements", "--extra", "--group"] },
	poetry: { subcommands: ["run"], valued: ["--directory", "-C", "--project", "-P"] },
} satisfies Record<string, ToolLauncher>;

// Parse command positions and known launchers, not quoted prose/arguments.
// Dynamic shell expansion is not evaluated: an unresolved directory fails closed for publishing.
// `scan` is shared with nested scripts and command substitutions so a variable touched anywhere
// marks every git action of the whole command. Substitutions never change the outer cwd.
async function gitActions(command: string, cwd: string | undefined, scan: { hookSkip?: string } = {}, depth = 0): Promise<GitAction[]> {
	if (depth > 16 || command.length > 131072) throw new Error("shell scan exceeds supported bounds");
	if (command.includes("\0")) throw new Error("shell scan does not support NUL bytes");
	const substitutions: string[] = [];
	// Keep substitutions opaque to the outer tokenizer, including those inside double quotes.
	// Find balanced syntax only; never execute expansions or infer their output.
	const endOf = (start: number, closing: string, nesting = 0): number => {
		if (nesting > 16) throw new Error("shell scan exceeds supported bounds");
		for (let i = start; i < command.length; i++) {
			const char = command[i];
			if (char === "\\" && closing !== "'") { i++; continue; }
			if (char === closing) return i;
			if (closing === "'") continue;
			if (char === "'" && closing !== '"') i = endOf(i + 1, "'", nesting + 1);
			else if (char === '"' && closing !== '"') i = endOf(i + 1, '"', nesting + 1);
			else if (char === "`" && closing !== "`") i = endOf(i + 1, "`", nesting + 1);
			else if (char === "$" && command[i + 1] === "(") i = endOf(i + 2, ")", nesting + 1);
			else if (char === "(" && closing === ")") i = endOf(i + 1, ")", nesting + 1);
		}
		throw new Error("unterminated shell quote or command substitution; cannot parse safely");
	};
	let shell = "";
	let quoted = false;
	for (let i = 0; i < command.length; i++) {
		const char = command[i];
		if (char === "\\") { shell += command.slice(i, i + 2); i++; }
		else if (char === "'" && !quoted) {
			const end = endOf(i + 1, "'");
			shell += command.slice(i, end + 1); i = end;
		} else if (char === '"') { quoted = !quoted; shell += char; }
		else if (char === "`" || (char === "$" && command[i + 1] === "(")) {
			const start = i + (char === "`" ? 1 : 2);
			const end = endOf(start, char === "`" ? "`" : ")");
			shell += `\0${substitutions.length}\0`;
			const script = command.slice(start, end);
			substitutions.push(char === "`" ? script.replace(/\\([$`\\])/g, "$1") : script);
			i = end;
		} else shell += char;
	}
	const tokens = shell.match(/(?:[^\s;&|()"'\\]+|\\.|"(?:\\.|[^"\\])*"|'[^']*')+|&&|\|\||[;&|()\n]/g) ?? [];
	const hasGitWord = (body: string) => {
		const source = body.replace(/\0(\d+)\0/g, (_match, index: string) => substitutions[Number(index)]);
		return /\b(?:git|gh)\b/.test(source) || /\b(?:git|gh)\b/.test(
			source.replace(/"((?:\\.|[^"\\])*)"|'([^']*)'|\\(.)/g,
				(_match, double: string | undefined, single: string | undefined, escaped: string | undefined) =>
					double?.replace(/\\(["\\$`])/g, "$1") ?? single ?? escaped ?? ""),
		);
	};
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
			// SAFETY: hasOwn restricts indexing to the static launcher's keys.
			const toolLauncher: ToolLauncher | undefined = Object.hasOwn(toolLaunchers, launcher) ?
				toolLaunchers[launcher as keyof typeof toolLaunchers] : undefined;
			if (toolLauncher && (!toolLauncher.subcommands.length || toolLauncher.subcommands.includes(words[1] ?? ""))) {
				words.shift();
				if (toolLauncher.subcommands.length) words.shift();
				if (toolLauncher.separator && !words.includes("--")) {
					throw new Error(`${launcher} exec/x requires -- before the wrapped command; cannot parse safely`);
				}
				const separator = words.indexOf("--");
				const git = words.findIndex(word => ["git", "gh"].includes(basename(word)));
				const explicitSeparator = toolLauncher.separator ||
					(["uv", "poetry"].includes(launcher) && separator >= 0 && (git < 0 || separator < git));
				while (words.length && (explicitSeparator || words[0].startsWith("-"))) {
					const option = words.shift()!;
					if (option === "--") break;
					const name = option.split("=")[0];
					const value = toolLauncher.valued.includes(name) ?
						(option.includes("=") ? option.slice(option.indexOf("=") + 1) : words.shift()) : undefined;
					if (launcher === "mise" && ["-C", "--cd"].includes(name)) cwd = await directory(value, cwd);
				}
				if (!["git", "gh"].includes(basename(words[0] ?? "")) &&
					words.some(word => ["git", "gh"].includes(basename(word)))) {
					throw new Error(`${launcher} wrapped command could not be parsed safely; put git first after options or use --`);
				}
				continue;
			}
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
					if (value) actions.push(...await gitActions([value, ...words.map(word => JSON.stringify(word))].join(" "), cwd, scan, depth + 1));
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
		if (executable === "alias" && words.some(word => word.includes("=") && hasGitWord(word.slice(word.indexOf("=") + 1)))) {
			throw new Error("alias definition contains a git/gh word; persistent shell aliases cannot be parsed safely");
		}
		if (["export", "unset", "declare", "typeset", "readonly", "local"].includes(executable)) words.forEach(touch);
		else if (executable === "eval") actions.push(...await gitActions(words.join(" "), cwd, scan, depth + 1));
		else if (["bash", "sh", "zsh"].includes(executable)) {
			while (words[0]?.startsWith("-")) {
				const option = words.shift()!;
				if (/^-[^-]*c/.test(option)) {
					if (words[0]) actions.push(...await gitActions(words[0], cwd, scan, depth + 1));
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
			if (words.some(word => ["-R", "--repo", "--hostname"].includes(word) || /^(-R.|--repo=|--hostname=)/.test(word))) {
				throw new Error("gh remote repository overrides are unsupported; use the validated local target repository");
			}
			actions.push({ command: `gh ${words.join(" ")}`, args: [...words], cwd, config: [] });
		}
		words = [];
	};
	const subshells: (string | undefined)[] = [];
	for (let index = 0; index < tokens.length; index++) {
		const token = tokens[index];
		let body = index;
		const commandStart = words.every(word => ["then", "do", "else", "{", "!"].includes(word));
		if (commandStart && token === "function" && /^[A-Za-z_]\w*$/.test(tokens[body + 1] ?? "")) body += 2;
		else if (commandStart && /^[A-Za-z_]\w*$/.test(token) && tokens[body + 1] === "(" && tokens[body + 2] === ")") body++;
		if (body !== index) {
			if (tokens[body] === "(" && tokens[body + 1] === ")") body += 2;
			while (tokens[body] === "\n") body++;
			if (tokens[body] === "{") {
				let braces = 1;
				let end = body + 1;
				for (; end < tokens.length && braces; end++) {
					if (tokens[end] === "{") braces++;
					if (tokens[end] === "}") braces--;
				}
				if (braces) throw new Error("unterminated shell function definition; cannot parse safely");
				if (hasGitWord(tokens.slice(body + 1, end - 1).join(" "))) {
					throw new Error("shell function definition contains a git/gh word; persistent shell functions cannot be parsed safely");
				}
				await flush();
				index = end - 1;
				continue;
			}
		}
		for (const match of token.matchAll(/\0(\d+)\0/g)) {
			actions.push(...await gitActions(substitutions[Number(match[1])], cwd, scan, depth + 1));
		}
		if (/^(?:&&|\|\||[;&|()\n])$/.test(token)) {
			await flush();
			if (token === "(") subshells.push(cwd);
			if (token === ")") cwd = subshells.pop();
		} else words.push(token.replace(/\0(\d+)\0/g, (_match, substitution: string) => `$(${substitutions[Number(substitution)]})`)
			.replace(/"((?:\\.|[^"\\])*)"|'([^']*)'|\\(.)/g,
				(_match, double: string | undefined, single: string | undefined, escaped: string | undefined) =>
					double?.replace(/\\(["\\$`])/g, "$1") ?? single ?? escaped ?? ""));
	}
	await flush();
	if (scan.hookSkip && !actions.length) actions.push({ command: "hook environment", args: [], cwd, config: [], hookSkip: scan.hookSkip });
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
	const operation = action.args[0];
	const valueShorts = operation === "commit" ? Object.keys(commitValueShorts)
		: operation === "merge" ? ["m", "F", "s", "X"]
		: operation === "rebase" ? ["x", "X", "C", "s"]
		: ["cherry-pick", "revert"].includes(operation) ? ["m", "X"] : [];
	const valueLongs = operation === "commit" ? Object.keys(commitValueLongs)
		: operation === "merge" ? ["--message", "--file", "--strategy", "--strategy-option", "--into-name"]
		: operation === "rebase" ? ["--exec", "--onto", "--strategy", "--strategy-option", "--whitespace"]
		: ["cherry-pick", "revert"].includes(operation) ? ["--mainline", "--strategy", "--strategy-option"]
		: operation === "am" ? ["--patch-format", "--directory", "--exclude", "--include", "--whitespace"] : [];
	for (let index = 0; index < args.length; index++) {
		const arg = args[index];
		if (arg === "--") break;
		if (arg.startsWith("--")) {
			const name = arg.split("=")[0];
			// Git accepts unambiguous long-option prefixes, so `--no-ver` means --no-verify and `--mes` --message.
			if (name.length > "--no-".length && "--no-verify".startsWith(name)) return arg;
			if (!arg.includes("=") && name.length > 2 && valueLongs.some(long => long.startsWith(name))) index++;
			continue;
		}
		if (!arg.startsWith("-") || arg === "-") continue;
		for (let at = 1; at < arg.length; at++) {
			const flag = arg[at];
			if (flag === "n" && action.args[0] === "commit") return arg;
			if (flag === "u" || flag === "S") break;
			if (valueShorts.includes(flag)) {
				if (at === arg.length - 1) index++;
				break;
			}
		}
	}
	const hooksPath = action.config.find(entry => entry.split("=")[0].toLowerCase() === "core.hookspath");
	if (hooksPath) return hooksPath;
	return action.hookSkip;
}

async function readRecord(root: string, configRoot: string): Promise<RepoRecord | undefined> {
	const dir = await findRecord(root, configRoot);
	if (dir) return { dir, gate: await loadGate(dir) };
}
function note(state: State, ctx: ExtensionContext, message: string): void {
	if (state.notes.has(message)) return;
	state.notes.add(message);
	ctx.ui.notify(message, "info");
}
export { SUBPROCESS_TIMEOUT_MS, stringList, mappingSchema, detailsSchema, policyEvent, fresh, absolute, canonicalPath, worktreeTarget, absentFile, repoRoot, pathArguments, paths, subagentPathReason, matches, missing, sentence, readReason, gitActions, commitBypass, readRecord, note };
export { fileErrorSchema };
export type { Mapping, RepoRecord, GitAction, State, ToolEvent };
