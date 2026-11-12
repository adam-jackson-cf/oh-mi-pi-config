import { access, lstat, readFile, realpath, rename, stat, writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve } from "node:path";
import {
	z, type ExtensionAPI, type ExtensionContext, type ToolCallEvent, type ToolResultEvent,
	type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent,
} from "@oh-my-pi/pi-coding-agent";
import {
	configRoot as defaultConfigRoot, findRecord, GATE_FILE, offText, RECORDS_DIR, readGateText, resolveRecord,
} from "../lib/repo-record.ts";

// The one hang guard for the rails' own short git/gh reads and the record's pathCheck.
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
	codeGlobs: stringList,
	editTools: stringList,
	gitCommand: regexpSource,
	pathFields: z.record(z.string(), stringList),
	resolveDevice: z.string(),
	rejectDevice: z.string(),
});
const argvSchema = z.array(z.string().refine(value => value.trim().length > 0)).min(1);
const nonEmpty = z.string().refine(value => value.trim().length > 0);
const retiredCiHost = "ciHost is retired; declare ci.requiredContext and ci.runner";
const policyFields = {
	pathCheck: argvSchema.optional(),
	testGlobs: stringList.optional(),
	agentMerge: z.enum(["allowed", "disabled"]).optional(),
	// `requiredContext` is the commit status or check run that must be SUCCESS on the exact PR head,
	// whoever posts it; `runner` is a role resolved by the machine's ci-runners.json.
	ci: z.object({ requiredContext: nonEmpty, runner: nonEmpty }).optional(),
	ciHost: z.unknown().optional(),
	// The repo's dynaflows switch (required); it replaces the retired project-level dynaflows file.
	dynaflows: z.unknown().optional(),
};
const dynaflowsSwitch = z.object({ enabled: z.boolean() });
// The per-repo record's gate.json (repos/<owner>/<name>/ under the oh-mi-pi-config root).
// `checks.commit` is the repo's own commit command: rails run it at the agent's commit, after
// pathCheck and before the harness checks; `checks.ci` runs in CI; `checks.format` runs in freeze()
// before hashing; `checks.profile` is validated for the harness's other consumers. Unknown fields
// (such as a retired dynaflowsContract or pathCheckTimeoutMs) are ignored, so older records load.
const gateSchema = z.object({
	schemaVersion: z.literal(2),
	checks: z.object({
		commit: argvSchema.optional(), ci: argvSchema.optional(), format: argvSchema.optional(), profile: argvSchema.optional(),
	}).optional(),
	...policyFields,
}).superRefine((gate, ctx) => {
	if (gate.ciHost !== undefined) ctx.addIssue({ code: "custom", path: ["ciHost"], message: retiredCiHost });
	if (!dynaflowsSwitch.safeParse(gate.dynaflows).success) {
		ctx.addIssue({ code: "custom", path: ["dynaflows"], message: 'dynaflows must be {"enabled": true|false}' });
	}
}).transform(gate => ({
	commit: gate.checks?.commit,
	ciCheck: gate.checks?.ci,
	pathCheck: gate.pathCheck,
	testGlobs: gate.testGlobs,
	agentMerge: gate.agentMerge,
	requiredCi: gate.ci,
	dynaflows: dynaflowsSwitch.parse(gate.dynaflows),
}));
const prCiSchema = z.object({
	headRefOid: z.string().regex(/^[0-9a-f]{40}$/),
	url: z.string().regex(/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/\d+$/),
	statusCheckRollup: z.array(z.union([
		z.object({ __typename: z.literal("CheckRun"), name: z.string(), status: z.string(), conclusion: z.union([z.string(), z.literal(null)]) }),
		z.object({ __typename: z.literal("StatusContext"), context: z.string(), state: z.string() }),
	])),
});

// Harness variables the omp runtime sets for its own commands. Rails strip them from every repo
// command they run (pathCheck, checks.commit, harness checks) so repos never see the harness.
const harnessVariables = ["AGENT"];
// Characters of a failed commit check's output shown to the agent (the tail, where findings end).
const commitOutputLimit = 8000;
// Derived from this file's location so the rails hold no machine path: <omp>/scripts/ci-runner.py.
const ciRunnerPath = fileURLToPath(new URL("../../scripts/ci-runner.py", import.meta.url));
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
const dynaflowsSchema = z.object({ schemaVersion: z.literal(1), enabled: z.boolean() });
type Dynaflows = z.infer<typeof dynaflowsSchema>;
const fileErrorSchema = z.object({ code: z.string().optional() });
type Mapping = z.infer<typeof mappingSchema>;
type Gate = z.infer<typeof gateSchema>;
type RepoRecord = { dir: string; gate: Gate };
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
	pointed: boolean;
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
	return {
		reads: new Set(), notes: new Set(), edits: new Set(), pathFailures: new Map(),
		pointed: false, proposal: [],
	};
}
function absolute(path: string, cwd: string): string | undefined {
	if (/^[a-z][\w+.-]*:\/\//i.test(path)) return;
	return resolve(cwd, path.startsWith("~/") ? resolve(homedir(), path.slice(2)) : path);
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

// The harness checks: omp-guardrails run from the harness's own reference project (never installed
// into the repo), over the whole repo, with the record as config. Rails append
// `--repo <worktree> --record <record dir>`.
const guardrailsProject = fileURLToPath(new URL("../skills/setup-repository-guardrails/assets/python-reference", import.meta.url));
const defaultGuardrailsCommand = ["uv", "run", "--project", guardrailsProject, "omp-guardrails", "check"];

// Dynaflows is on for a repo only when its per-repo record (resolved from the origin remote under
// `configRoot`/repos/) says dynaflows.enabled true. `machineDynaflowsPath` (~/.omp/dynaflows.json)
// is the machine default: {"enabled": false} turns dynaflows off for every repo on this machine;
// absent or true defers to the record. No origin or no record means off. `configPath` is the rails
// mapping file (agent/agent-rails.yml beside this extension).
export default function agentRails(
	pi: ExtensionAPI,
	{
		machineDynaflowsPath = fileURLToPath(new URL("../../dynaflows.json", import.meta.url)),
		configPath = fileURLToPath(new URL("../agent-rails.yml", import.meta.url)),
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
	const note = (state: State, ctx: ExtensionContext, message: string) => {
		if (state.notes.has(message)) return;
		state.notes.add(message);
		ctx.ui.notify(message, "info");
	};
	// The repo's per-repo record, resolved from its origin remote; undefined when it has none. An
	// unreadable or invalid record throws.
	async function recordAt(root: string): Promise<RepoRecord | undefined> {
		const dir = await findRecord(root, configRoot);
		if (!dir) return;
		const path = join(dir, GATE_FILE);
		const text = await readGateText(dir);
		let parsed;
		try { parsed = gateSchema.safeParse(JSON.parse(text)); }
		catch (error) { throw new Error(`${path}: ${String(error)}`); }
		if (!parsed.success) throw new Error(`${path}: invalid gate: ${parsed.error.message}`);
		return { dir, gate: parsed.data };
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
	// True when `path` lies lexically under the records root (<config root>/repos/). Symlink aliases
	// and bash writes are not followed (accepted for a single owner).
	function underRecords(path: string): boolean {
		const rel = relative(resolve(configRoot, RECORDS_DIR), resolve(path));
		return rel !== "" && !rel.startsWith("..") && !isAbsolute(rel);
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
	// `$dynaflows on|off` sets the record's switch; it never creates a record or touches the repo.
	async function setDynaflows(ctx: ExtensionContext, enabled: boolean): Promise<string> {
		const root = await repoRoot(ctx.cwd);
		if (!root) throw new Error("repo toggle needs a Git project; edit the machine default separately");
		const dir = await findRecord(root, configRoot);
		if (!dir) throw new Error(`${root} has no per-repo record (resolved from its origin remote); draft one with skill://setup-repository-guardrails first`);
		const path = join(dir, GATE_FILE);
		const gate = z.record(z.string(), z.unknown()).parse(JSON.parse(await readGateText(dir)));
		gate.dynaflows = { enabled };
		const temp = `${path}.${randomUUID()}.tmp`;
		await writeFile(temp, `${JSON.stringify(gate, null, 2)}\n`);
		await rename(temp, path);
		return path;
	}
	// Records hold the owner's policy and approvals and never reach a PR, so the owner edits them by
	// hand: rails refuse every edit-tool write under the records root.
	function recordWriteReason(targets: string[]): string | undefined {
		for (const path of targets) {
			if (underRecords(path)) return `Agent rails: ${path} is under the per-repo records root; agents never write records. ` +
				"Print the drafted change (or write it outside repos/) and ask the owner to save it by hand.";
		}
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
			const status = await pi.exec("git", ["status", "--porcelain=v1", "-z", "--untracked-files=all"], { cwd: canonicalRoot, timeout: SUBPROCESS_TIMEOUT_MS });
			if (status.code !== 0 || status.killed) {
				throw new Error(`git status failed${status.killed ? " (killed)" : ` (exit ${status.code})`}: ${status.stderr || status.stdout}`.trim());
			}
			const changedPaths = new Set<string>();
			const records = status.stdout.split("\0");
			for (let index = 0; index < records.length; index++) {
				const record = records[index];
				if (!record) continue;
				changedPaths.add(record.slice(3));
				// In NUL-delimited porcelain, rename/copy destinations precede their source path.
				if (record.slice(0, 2).includes("R") || record.slice(0, 2).includes("C")) index++;
			}
			changed = changed.filter(path => changedPaths.has(relative(canonicalRoot, path)));
			if (!changed.length) { state.pathFailures.delete(root); return; }
			const result = await repoExec([...gate.pathCheck, ...changed], canonicalRoot, SUBPROCESS_TIMEOUT_MS);
			if (result.code !== 0 || result.killed) throw new Error(`${result.stdout}\n${result.stderr}`.trim());
			state.pathFailures.delete(root);
		} catch (error) {
			state.pathFailures.set(root, changed);
			return `Agent rails: pathCheck failed in ${root}: ${String(error)}`;
		}
	}
	// Repo paths a commit can take: staged and unstaged tracked changes, as absolute paths.
	async function commitPaths(root: string): Promise<string[]> {
		const found = new Set<string>();
		for (const args of [["diff", "--cached", "--name-only", "-z"], ["diff", "--name-only", "-z"]]) {
			const result = await pi.exec("git", args, { cwd: root, timeout: SUBPROCESS_TIMEOUT_MS });
			if (result.code !== 0 || result.killed) throw new Error(`git ${args.join(" ")} failed: ${result.stderr}`.trim());
			for (const path of result.stdout.split("\0")) if (path) found.add(resolve(root, path));
		}
		return [...found];
	}
	// At the agent's commit in a dynaflows-on repo: the record's pathCheck over the paths the commit
	// can take, the record's checks.commit (the repo's own command), then the harness checks over the
	// whole repo with the record as config. Any failure or finding blocks the commit; there are no
	// baselines and no diff scoping.
	async function commitChecksReason(root: string, state: State): Promise<string | undefined> {
		const record = await recordAt(root);
		if (!record) return;
		if (record.gate.pathCheck) {
			const changed = [...new Set([...(state.pathFailures.get(root) ?? []), ...await commitPaths(root)])];
			const failed = await checkPaths(root, changed, record.gate, state);
			if (failed) return `${failed}\nFix the findings, then commit again.`;
		}
		const runs: [string, string[]][] = [
			...(record.gate.commit ? [[`checks.commit (${record.gate.commit.join(" ")})`, record.gate.commit] satisfies [string, string[]]] : []),
			["harness checks (omp-guardrails)", [...guardrailsCommand, "--repo", root, "--record", record.dir]],
		];
		for (const [label, argv] of runs) {
			const result = await repoExec(argv, root);
			if (result.code === 0 && !result.killed) continue;
			const output = `${result.stdout}\n${result.stderr}`.trim();
			return `Agent rails: ${label} failed in ${root}${result.killed ? " (killed)" : ` (exit ${result.code})`}; ` +
				`fix every finding in the repo, then commit again:\n${output.length > commitOutputLimit ? `…${output.slice(-commitOutputLimit)}` : output}`;
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
					if (action.hookSkip) return { block: true, reason: `Agent rails: pre-commit hook environment mutation is forbidden: ${action.hookSkip}` };
					// Pulling commits in needs nothing beyond the hooks the commits it makes run.
					if (/^git (?:merge|rebase|cherry-pick|revert|am|pull)(?![-\w])/.test(action.command)) {
						const bypass = commitBypass(action);
						if (bypass) return { block: true, reason: `Agent rails: drop hook bypass ${bypass}` };
						continue;
					}
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
						const at = root ? await dynaflowsAt(ctx, root, true) : await dynaflowsAt(ctx, action.cwd);
						// Fail-open is never silent: every commit outside dynaflows says why.
						if (!at.on && at.off) ctx.ui.notify(`Agent rails: ${at.off}; commit checks do not run.`, "info");
						if (root && at.on) {
							const reason = await commitChecksReason(root, state);
							if (reason) return { block: true, reason };
						}
					}
					if (/^(?:git push\b|gh pr (?:create|merge)\b)/.test(action.command)) {
						if (!action.cwd) return {
							block: true, reason: "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push or gh pr create/merge.",
						};
						const root = await repoRoot(action.cwd);
						if (!root) throw new Error("publishing requires a validated local Git repository");
						const gate = root ? await gateAt(root, state, ctx, true) : undefined;
						if (gate?.agentMerge === "disabled") return {
							block: true, reason: "Agent rails: agentMerge is disabled; ask the owner before git push, gh pr create or gh pr merge in this repository.",
						};
						// With dynaflows on, a merge needs CI green on the PR head and prove()'s pass for that head.
						if (/^gh pr merge\b/.test(action.command) && await dynaflowsOn(ctx, action.cwd)) {
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
			if (ctx.agent.kind !== "main") {
				const reason = await subagentPathReason([
					...pathArguments(event, map),
					...(event.input.path === map.resolveDevice ? state.proposal : []),
				]);
				if (reason) return { block: true, reason };
			}
			const targets = event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
			const recordWrite = recordWriteReason(targets);
			if (recordWrite) return { block: true, reason: recordWrite };
			const required = new Set<string>();
			for (const path of targets) {
				const root = await repoRoot(path);
				const gate = root ? await gateAt(root, state, ctx) : undefined;
				if (gate?.pathCheck) continue;
				const rel = relative(root ?? ctx.cwd, path);
				const triggers: string[] = [];
				if (!state.edits.size && matches(rel, map.codeGlobs)) triggers.push("firstEdit");
				// New-file reads apply only to code files inside a repository, not scratch paths.
				if (root && matches(rel, map.codeGlobs) && !(await access(path).then(() => true, () => false))) triggers.push("newFile");
				if (matches(rel, gate?.testGlobs ?? map.testGlobs)) triggers.push("test");
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
		const directives = (event.prompt ?? "").split("\n").map(line => /^\$dynaflows(?: (on|off))?$/i.exec(line.trim())).filter(match => match !== null);
		const settings = new Set(directives.map(match => match[1]?.toLowerCase() !== "off"));
		if (settings.size > 1) { ctx.ui.notify("Agent rails: contradictory dynaflows directives; no setting changed.", "error"); return; }
		if (directives.length) {
			const enabled = directives[0][1]?.toLowerCase() !== "off";
			let path: string;
			try { path = await setDynaflows(ctx, enabled); }
			catch (error) { ctx.ui.notify(`Agent rails: ${String(error)}`, "error"); return; }
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
		const record = root ? await recordAt(root).catch(() => undefined) : undefined;
		if (record && !(record.gate.commit && record.gate.pathCheck)) lines.push(
			`The per-repo record ${record.dir} lacks checks.commit or pathCheck.`,
			"Before guardrail work, ask the owner whether to complete it with skill://setup-repository-guardrails,",
			"which drafts the record for owner confirmation and writes nothing into the repo except fixes.",
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
