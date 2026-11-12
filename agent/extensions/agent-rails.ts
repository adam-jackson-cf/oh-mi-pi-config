import { access, lstat, mkdir, readdir, readFile, readlink, realpath, rename, rm, stat, symlink, writeFile } from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { homedir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import {
	z, type ExtensionAPI, type ExtensionContext, type ToolCallEvent, type ToolResultEvent,
	type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent,
} from "@oh-my-pi/pi-coding-agent";
import {
	configRoot as defaultConfigRoot, findRecord, GATE_FILE, offText, RECORDS_DIR, readGateText, resolveRecord,
} from "../lib/repo-record.ts";

// Hang guard for the rails' own short git/gh reads. CI waits never use it: their timeouts come
// from the runner config. gate.json `pathCheckTimeoutMs` overrides it for the path check.
const SUBPROCESS_TIMEOUT_MS = 20_000;
const stringList = z.array(z.string());
const regexpSource = z.string().refine(value => {
	try { new RegExp(value); return true; } catch { return false; }
});
const contractVersion = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const mappingSchema = z.object({
	// The dynaflows contract this omp install implements; repos record theirs in gate.json.
	dynaflowsContract: contractVersion,
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
	pathCheckTimeoutMs: z.number().int().positive().max(Number.MAX_SAFE_INTEGER).optional(),
	testGlobs: stringList.optional(),
	agentMerge: z.enum(["allowed", "disabled"]).optional(),
	// `requiredContext` is the commit status or check run that must be SUCCESS on the exact PR head,
	// whoever posts it; `runner` is a role resolved by the machine's ci-runners.json.
	ci: z.object({ requiredContext: nonEmpty, runner: nonEmpty }).optional(),
	ciHost: z.unknown().optional(),
	// The dynaflows contract the record was set up against; rails and workflows refuse a lower one.
	dynaflowsContract: contractVersion.optional(),
	// The repo's dynaflows switch (required); it replaces the retired project-level dynaflows file.
	dynaflows: z.unknown().optional(),
};
const dynaflowsSwitch = z.object({ enabled: z.boolean() });
// The per-repo record's gate.json (repos/<owner>/<name>/ under the oh-mi-pi-config root).
// `checks.commit` is the repo's own commit command: rails run it at the agent's commit, then the
// harness checks; `checks.ci` runs in CI for push and merge; `checks.format` runs in freeze()
// before hashing; `checks.profile` runs first in prove().
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
	pathCheckTimeoutMs: gate.pathCheckTimeoutMs,
	testGlobs: gate.testGlobs,
	agentMerge: gate.agentMerge,
	requiredCi: gate.ci,
	dynaflowsContract: gate.dynaflowsContract,
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
const agentVerified = "agent-verified";
const agentVerifiedStatusOnly = "Agent rails: agent-verified is a SHA-bound commit status; prove() posts it (non-dynaflows: gh api repos/OWNER/REPO/statuses/SHA -f state=success -f context=agent-verified)";
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
	// Git's global options before the subcommand, e.g. -C, -c, --exec-path=… (git actions only).
	globals?: string[];
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
// For record scans: a missing path, or one of the wrong kind (file vs directory), holds nothing.
function absentEntry(error: NodeJS.ErrnoException): null {
	const parsed = fileErrorSchema.safeParse(error);
	if (parsed.success && ["ENOENT", "ENOTDIR", "EISDIR"].includes(parsed.data.code ?? "")) return null;
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
			const globals: string[] = [];
			while (words[0]?.startsWith("-")) {
				const option = words.shift()!;
				globals.push(option);
				if (["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"].includes(option)) {
					const value = words.shift();
					if (option === "-C") effective = await directory(value, effective);
					if ((option === "-c" || option === "--config-env") && value) config.push(value);
				} else if (option.startsWith("--config-env=")) config.push(option.slice("--config-env=".length));
				else if (option.startsWith("-c") && option.length > 2) config.push(option.slice(2));
				else if (option.startsWith("-C") && option.length > 2) effective = await directory(option.slice(2), effective);
			}
			actions.push({ command: `git ${words.join(" ")}`, args: [...words], cwd: effective, config, globals });
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

// Rails run commit checks at tool-call time, before the command runs. So a checked commit must be
// the whole command: one `git` (or tool-launcher) invocation with no unquoted operator, subshell,
// redirection or substitution, no shell, eval or xargs wrapper, and no leading VAR=value assignment.
function soleCommit(command: string): boolean {
	let plain = "";
	for (let i = 0; i < command.length; i++) {
		const char = command[i];
		if (char === "\\") { plain += "__"; i++; continue; }
		if (char !== "'" && char !== '"') { plain += char; continue; }
		const end = command.indexOf(char, i + 1);
		if (end < 0) return false;
		const body = command.slice(i + 1, end);
		if (char === '"' && (/[`\\]/.test(body) || body.includes("$("))) return false;
		plain += "_".repeat(body.length + 2);
		i = end;
	}
	if (/[;&|()<>`{}\n]/.test(plain.trim())) return false;
	const executable = basename(plain.trim().split(/\s+/)[0] ?? "");
	return executable === "git" || Object.hasOwn(toolLaunchers, executable);
}
// Which `git commit` arguments (after "commit") stage everything (-a/--all), give the message
// (-m/--message/-F/--file) or may open an editor (-e/--edit, -c/--reedit-message).
function commitOptions(args: string[]) {
	const seen = { all: false, message: false, edit: false };
	for (let index = 0; index < args.length; index++) {
		const arg = args[index];
		if (arg === "--") break;
		if (arg.startsWith("--")) {
			const name = arg.split("=")[0];
			if (name === "--all") seen.all = true;
			if (["--message", "--file"].includes(name)) seen.message = true;
			if (name.length > 2 && ("--edit".startsWith(name) || "--reedit-message".startsWith(name))) seen.edit = true;
			if (!arg.includes("=") && name.length > 2 && Object.keys(commitValueLongs).some(long => long.startsWith(name))) index++;
			continue;
		}
		if (!arg.startsWith("-") || arg === "-") continue;
		for (let at = 1; at < arg.length; at++) {
			const flag = arg[at];
			if (flag === "a") seen.all = true;
			if (flag === "e" || flag === "c") seen.edit = true;
			if (flag === "m" || flag === "F") seen.message = true;
			if (flag === "u" || flag === "S") break;
			if (Object.hasOwn(commitValueShorts, flag)) {
				if (at === arg.length - 1) index++;
				break;
			}
		}
	}
	return seen;
}
// Why a dynaflows commit could run a program the commit checks never saw, if it could.
function checkedCommitReason(command: string, action: GitAction): string | undefined {
	if (!soleCommit(command)) return "Agent rails: commit checks run before the command, so `git commit` must be the whole command. Stage your changes first (git add) in a separate call, then run `git commit` on its own: no &&, ||, ;, |, newline, subshell, $(...), backticks, redirection, leading VAR=value assignment or sh -c/eval/xargs wrapper; use the cwd parameter or git -C instead of cd.";
	const global = action.globals?.find(option => /^(?:-c|--config-env|--exec-path)/.test(option));
	if (global) return `Agent rails: dynaflows commits refuse git global option ${global}, which can make the commit run other programs; drop it and commit again.`;
	const options = commitOptions(action.args.slice(1));
	if (!options.message || options.edit) return "Agent rails: dynaflows commits must give the message with -m/--message or -F/--file and must not open an editor (-e/--edit, -c/--reedit-message); commit again with -m.";
}

type Author = { family: string; model: string; agent: string };
type AuthorScope = { worktree: string; common: string; branch: string; ledger: string };
const authorText = z.string().refine(value => value.trim().length > 0);
// Rails refuse a record whose contract differs from the installed one (absent, below or above):
// strict callers block, the rest note it and treat dynaflows as off. workflows.py
// contract_mismatch() states the same text.
function contractMismatch(recorded: number | undefined, current: number): string | undefined {
	if (recorded === current) return;
	const stated = `Dynaflows contract v${recorded ?? "none"} recorded; current is v${current}.`;
	return recorded !== undefined && recorded > current
		? `${stated} The record expects a newer dynaflows contract; update omp.`
		: `${stated} Draft the update with omp-guardrails init --extend --record <a copy of the record outside repos/>, ` +
			"then ask the owner to review it and save it over the live record; agents never write records.";
}
const authorSchema = z.object({ family: authorText, model: authorText, agent: authorText });
const ledgerSchema = z.object({
	schemaVersion: z.literal(1), worktree: z.string(), branch: z.string(), authors: z.array(authorSchema).min(1),
});
function compareAuthors(a: Author, b: Author): number {
	for (const key of ["family", "model", "agent"] as const) {
		if (a[key] !== b[key]) return a[key] < b[key] ? -1 : 1;
	}
	return 0;
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
	// Factories are rebound to child sessions; only the parent-keyed authorship registry is shared.
	const states = new Map<string, State>();
	// Record file texts taken when a non-edit tool call starts, keyed by tool call ID.
	const recordSnapshots = new Map<string, Map<string, { link?: string; data?: Buffer }>>();
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
	async function scopeAt(cwd: string) {
		const worktree = await realpath(await gitValue(cwd, ["rev-parse", "--show-toplevel"]));
		const common = await realpath(resolve(worktree, await gitValue(worktree, ["rev-parse", "--git-common-dir"])));
		const branchResult = await pi.exec("git", ["symbolic-ref", "-q", "HEAD"], { cwd: worktree, timeout: SUBPROCESS_TIMEOUT_MS });
		const branch = branchResult.code === 0 ? branchResult.stdout.trim() : `detached:${await gitValue(worktree, ["rev-parse", "HEAD"])}`;
		const key = createHash("sha256").update(`${worktree}\0${branch}`).digest("hex");
		return { worktree, common, branch, ledger: resolve(common, "omp-authors", `${key}.json`) };
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
	async function externalAuthors(scope: AuthorScope, parent?: string): Promise<Author[]> {
		const repository = await primaryGithubRepository(scope.worktree);
		async function github(args: string[]): Promise<string> {
			const result = await pi.exec("gh", args, { cwd: scope.worktree, timeout: SUBPROCESS_TIMEOUT_MS });
			if (result.code !== 0 || result.killed) throw new Error(`gh failed: ${result.stderr}`);
			return result.stdout;
		}
		const head = await gitValue(scope.worktree, ["rev-parse", "HEAD"]);
		const prs = z.array(z.object({
			author: z.object({ login: z.string() }).nullable(), headRefName: z.string(), headRefOid: z.string(),
		})).parse(JSON.parse(await github(["pr", "list", "--search", head, "--state", "open", "--json", "author,headRefName,headRefOid"])));
		const matches = prs.filter(pr => pr.headRefOid === head);
		if (matches.length !== 1) throw new Error("no unique open PR at requested head");
		const pr = matches[0];
		if (!pr.author || !["app/dependabot", "dependabot[bot]"].includes(pr.author.login)) throw new Error("PR author is not Dependabot");
		if (!pr.headRefName.startsWith("dependabot/")) throw new Error("PR head branch is not dependabot/");
		if (!parent) {
			const base = z.object({ baseRefOid: z.string().regex(/^[0-9a-f]{40}$/) }).parse(
				JSON.parse(await github(["pr", "view", pr.headRefName, "--json", "baseRefOid"])));
			parent = await gitValue(scope.worktree, ["merge-base", base.baseRefOid, head]);
		}
		const ancestry = await pi.exec("git", ["merge-base", "--is-ancestor", parent, head], { cwd: scope.worktree, timeout: SUBPROCESS_TIMEOUT_MS });
		if (ancestry.code !== 0 || ancestry.killed) throw new Error("external bot parent is not an ancestor of head");
		const range = await pi.exec("git", ["rev-list", `${parent}..${head}`], { cwd: scope.worktree, timeout: SUBPROCESS_TIMEOUT_MS });
		if (range.code !== 0 || range.killed) throw new Error(`cannot resolve external bot commit range: ${range.stderr}`);
		if (!range.stdout.trim()) throw new Error("empty external bot commit range");
		const commits = range.stdout.trim().split("\n");
		for (const sha of commits) {
			const commit = z.object({
				author: z.object({ login: z.string() }).nullable(),
				commit: z.object({ verification: z.object({ verified: z.boolean() }) }),
			}).parse(JSON.parse(await github(["api", `repos/${repository}/commits/${sha}`])));
			if (commit.author?.login !== "dependabot[bot]") throw new Error(`commit ${sha} author is not dependabot[bot]`);
			if (!commit.commit.verification.verified) throw new Error(`commit ${sha} is not verified`);
		}
		return [{ family: "external", model: "dependabot[bot]", agent: "dependabot[bot]" }];
	}
	async function authorsAt(scope: AuthorScope, required: boolean, parent?: string): Promise<Author[]> {
		try {
			const ledger = ledgerSchema.parse(JSON.parse(await readFile(scope.ledger, "utf8")));
			if (ledger.worktree !== scope.worktree || ledger.branch !== scope.branch) throw new Error("author ledger scope mismatch");
			if (ledger.authors.some((author, index) => index > 0 && compareAuthors(ledger.authors[index - 1], author) >= 0)) {
				throw new Error("author ledger must be sorted and deduplicated");
			}
			return ledger.authors;
		} catch (error) {
			const parsed = fileErrorSchema.safeParse(error);
			if (parsed.success && parsed.data.code === "ENOENT") {
				if (!required) return [];
				try { return await externalAuthors(scope, parent); }
				catch (reason) { throw new Error(`no author ledger for ${scope.worktree} ${scope.branch}; authorship unknown: ${String(reason)}`); }
			}
			throw new Error(`authorship unknown for ${scope.worktree} ${scope.branch}: ${String(error)}`);
		}
	}
	async function recordAuthor(ctx: ExtensionContext, root: string) {
		const model = ctx.models.current();
		if (!model) throw new Error("cannot resolve the editing agent's model family");
		const scope = await scopeAt(root);
		const authors = await authorsAt(scope, false);
		authors.push({ family: ctx.models.family(model), model: `${model.provider}/${model.id}`, agent: ctx.agent.id ?? ctx.sessionManager.getSessionId() });
		const sorted = [...new Map(authors.map(author => [JSON.stringify(author), author])).values()]
			.sort(compareAuthors);
		await mkdir(dirname(scope.ledger), { recursive: true });
		const temp = `${scope.ledger}.${randomUUID()}.tmp`;
		await writeFile(temp, `${JSON.stringify({ schemaVersion: 1, worktree: scope.worktree, branch: scope.branch, authors: sorted })}\n`);
		await rename(temp, scope.ledger);
	}
	const note = (state: State, ctx: ExtensionContext, message: string) => {
		if (state.notes.has(message)) return;
		state.notes.add(message);
		ctx.ui.notify(message, "info");
	};
	// The repo's per-repo record, resolved from its origin remote; undefined when it has none. An
	// unreadable or invalid record, or one below the installed contract, throws.
	async function recordAt(root: string): Promise<RepoRecord | undefined> {
		const dir = await findRecord(root, configRoot);
		if (!dir) return;
		const path = join(dir, GATE_FILE);
		const text = await readGateText(dir);
		let parsed;
		try { parsed = gateSchema.safeParse(JSON.parse(text)); }
		catch (error) { throw new Error(`${path}: ${String(error)}`); }
		if (!parsed.success) throw new Error(`${path}: invalid gate: ${parsed.error.message}`);
		const contract = contractMismatch(parsed.data.dynaflowsContract, map.dynaflowsContract);
		if (contract) throw new Error(`${path}: ${contract}`);
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
	// The canonical form of `target`: symlinks resolved, including a dangling final link and missing
	// descendants of an existing directory.
	async function canonicalPath(target: string, depth = 0): Promise<string> {
		try { return await realpath(target); }
		catch (error) {
			const parsed = fileErrorSchema.safeParse(error);
			if (!parsed.success || parsed.data.code !== "ENOENT") throw error;
		}
		const link = await readlink(target).catch(() => undefined);
		if (link !== undefined) {
			if (depth > 40) throw new Error(`too many symlinks resolving ${target}`);
			return canonicalPath(resolve(dirname(target), link), depth + 1);
		}
		const parent = dirname(target);
		return parent === target ? target : join(await canonicalPath(parent, depth), basename(target));
	}
	// True when `path` lies under the records root (<config root>/repos/), compared both as written
	// and through symlinks, so an alias of a record file is a record file.
	async function underRecords(path: string): Promise<boolean> {
		const records = resolve(configRoot, RECORDS_DIR);
		for (const [base, file] of [[records, resolve(path)], [await canonicalPath(records), await canonicalPath(resolve(path))]]) {
			const rel = relative(base, file);
			if (rel && !rel.startsWith("..") && !isAbsolute(rel)) return true;
		}
		return false;
	}
	// Every entry under the records root: a file's bytes or a symlink's target, keyed by path. Tools
	// other than the edit tools (bash above all) can write anywhere, so rails compare the records
	// before and after each such call.
	async function recordEntries(): Promise<Map<string, { link?: string; data?: Buffer }>> {
		const found = new Map<string, { link?: string; data?: Buffer }>();
		const walk = async (dir: string) => {
			for (const entry of (await readdir(dir, { withFileTypes: true }).catch(absentEntry)) ?? []) {
				const path = join(dir, entry.name);
				if (entry.isDirectory()) await walk(path);
				else if (entry.isSymbolicLink()) found.set(path, { link: await readlink(path) });
				else if (entry.isFile()) found.set(path, { data: await readFile(path) });
			}
		};
		await walk(resolve(configRoot, RECORDS_DIR));
		return found;
	}
	// Restores every record entry a non-edit tool call added, changed or removed, and says which.
	async function revertRecordChanges(before: Map<string, { link?: string; data?: Buffer }>): Promise<string | undefined> {
		const after = await recordEntries();
		const same = (a?: { link?: string; data?: Buffer }, b?: { link?: string; data?: Buffer }) =>
			a?.link === b?.link && (a?.data === b?.data || (a?.data !== undefined && b?.data !== undefined && a.data.equals(b.data)));
		const restored: string[] = [];
		for (const path of new Set([...before.keys(), ...after.keys()])) {
			const previous = before.get(path);
			if (same(previous, after.get(path))) continue;
			await rm(path, { force: true, recursive: true });
			if (previous?.link !== undefined) {
				await mkdir(dirname(path), { recursive: true });
				await symlink(previous.link, path);
			} else if (previous?.data !== undefined) {
				await mkdir(dirname(path), { recursive: true });
				await writeFile(path, previous.data);
			}
			restored.push(path);
		}
		if (restored.length) return `Agent rails: this tool call changed per-repo record files (${restored.join(", ")}); ` +
			"rails restored each to its state before the call. Agents never write records: print the drafted change " +
			"or write it outside repos/, and ask the owner to save it by hand.";
	}
	async function passMarkerReason(cwd: string, head: string): Promise<string | undefined> {
		const common = await pi.exec("git", ["rev-parse", "--git-common-dir"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (common.code !== 0 || common.killed) throw new Error("cannot resolve Git common directory for pass marker");
		const path = resolve(cwd, common.stdout.trim(), "omp-prove", `${head}.json`);
		const marker = z.object({
			schemaVersion: z.literal(1), head: z.literal(head), status: z.literal("pass"), kind: z.enum(["prove", "docs-only"]),
		});
		try {
			if (marker.safeParse(JSON.parse(await readFile(path, "utf8"))).success) return;
		} catch { /* Missing or corrupt proof is not a pass. */ }
		return `Agent rails: no valid pass marker for ${head} at ${path}; run prove() or exempt_docs_only() for this head`;
	}
	type PrCi = { head?: string; repository?: string; reason?: string; agentVerified?: string };
	// Reads the PR's exact head and its checks. `agent-verified` is evidence, not CI: it is reported
	// separately and never counts towards (or against) the CI rule.
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
		const evidence = pr.statusCheckRollup.find(check => check.__typename === "StatusContext" && check.context === agentVerified);
		const agentVerifiedState = evidence?.__typename === "StatusContext" ? evidence.state : "missing";
		const checks = pr.statusCheckRollup.filter(check => !(check.__typename === "StatusContext" && check.context === agentVerified));
		const stateOf = (check: typeof checks[number]) => check.__typename === "StatusContext"
			? check.state : `${check.status}/${check.conclusion ?? ""}`;
		const green = (check: typeof checks[number]) => check.__typename === "StatusContext"
			? check.state === "SUCCESS" : check.status === "COMPLETED" && check.conclusion === "SUCCESS";
		if (gate?.requiredCi) {
			const { requiredContext, runner } = gate.requiredCi;
			const named = checks.filter(check => (check.__typename === "StatusContext" ? check.context : check.name) === requiredContext);
			if (!named.some(green)) {
				const command = (verb: string) => `python3 ${shellWord(ciRunnerPath)} ${verb} ${shellWord(runner)} ${repository} ${head}`;
				return { head, repository, agentVerified: agentVerifiedState, reason:
					`Agent rails: required CI context "${requiredContext}" is ${named.length ? stateOf(named[0]) : "missing"} for ${head} in ${repository} (runner ${runner}). ` +
					`Submit and wait: ${command("submit")}, then ${command("wait")} --context ${shellWord(requiredContext)}; ` +
					"see skill://git-tool/references/merge-and-recover.md for failures." };
			}
			// A declared context alone decides; other reported checks (optional apps, previews) never block.
			return { head, repository, agentVerified: agentVerifiedState };
		}
		// No ci block declared: every reported check must be green.
		if (!checks.length) {
			const workflows = await stat(resolve(root, ".github/workflows")).catch(absentFile);
			return { head, repository, agentVerified: agentVerifiedState, reason: gate?.ciCheck || workflows?.isDirectory()
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
		return { head, repository, agentVerified: agentVerifiedState, reason: nonGreen.length || !checks.some(green)
			? `Agent rails: CI is not green for ${head}: ${nonGreen.join(", ") || "no SUCCESS check reported"}` : undefined };
	}
	// The open PR whose head is exactly `sha`; status posts are judged by that PR's checks.
	async function prAtHead(cwd: string, sha: string): Promise<{ selector?: string; reason?: string }> {
		const result = await pi.exec("gh", ["pr", "list", "--search", sha, "--state", "open", "--json", "number,headRefOid"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code !== 0 || result.killed) return {
			reason: `Agent rails: cannot find the PR for ${sha} via gh: ${result.stderr.trim()}; authenticate the GitHub account that owns this repository (gh auth switch) and retry.`,
		};
		const prs = z.array(z.object({ number: z.number().int(), headRefOid: z.string() })).parse(JSON.parse(result.stdout))
			.filter(pr => pr.headRefOid === sha);
		if (prs.length !== 1) return { reason: `Agent rails: ${prs.length ? "several open PRs have" : "no open PR has"} head ${sha}; agent-verified is posted only on the exact head of one open PR.` };
		return { selector: String(prs[0].number) };
	}
	// `gh api .../statuses/<sha>` posting the agent-verified context: allowed only when the PR whose
	// head is <sha> has green exact-head CI (and, with dynaflows on, a valid pass marker for <sha>).
	async function statusPostReason(action: GitAction, state: State, ctx: ExtensionContext): Promise<string | undefined> {
		const endpoint = action.args.slice(1).map(arg => /(?:^|\/)repos\/([^/\s]+)\/([^/\s]+)\/statuses\/([^/?#\s]*)/.exec(arg)).find(match => match);
		if (!endpoint) return;
		const method = action.args.findIndex(arg => ["-X", "--method"].includes(arg));
		const verb = method >= 0 ? action.args[method + 1] : action.args.find(arg => /^(?:-X|--method=)/.test(arg))?.replace(/^(?:-X|--method=)/, "");
		if (verb?.toUpperCase() === "GET") return;
		// A payload rails cannot read (file input or @file values) may carry the agent-verified context.
		if (action.args.some(arg => arg === "--input" || arg.startsWith("--input=") || /^[^=]+=@/.test(arg))) {
			return "Agent rails: commit status posts must pass fields with -f/-F literals (no --input or @file), so rails can check the context.";
		}
		if (!action.args.some(arg => arg.includes(agentVerified))) return;
		const [, owner, name, sha] = endpoint;
		if (/[$`]/.test(sha)) return `Agent rails: received unexpanded shell text \`${sha}\`; rails check the literal command, so pass the 40-hex SHA itself`;
		if (!/^[0-9a-f]{40}$/.test(sha)) return `Agent rails: agent-verified status needs a lowercase 40-hex SHA, got ${sha}`;
		if (!action.cwd) throw new Error("cannot resolve status post cwd");
		const root = await repoRoot(action.cwd);
		if (!root) throw new Error("status posts require a validated local Git repository");
		const checkout = await primaryGithubRepository(root);
		if (!(owner === "{owner}" && name === "{repo}") && checkout.toLowerCase() !== `${owner}/${name}`.toLowerCase()) {
			return `Agent rails: run from a checkout of ${owner}/${name}`;
		}
		const found = await prAtHead(action.cwd, sha);
		if (found.reason) return found.reason;
		const pr = await prCi(action.cwd, found.selector, state, ctx);
		if (pr.reason) return pr.reason;
		if (pr.head !== sha) return `Agent rails: PR head is ${pr.head}, not ${sha}; re-verify the current head`;
		if (await dynaflowsOn(ctx, action.cwd)) return passMarkerReason(action.cwd, sha);
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
	// hand: rails refuse every edit-tool write under the records root. Other tools are checked after
	// they run (`revertRecordChanges`).
	async function recordWriteReason(targets: string[]): Promise<string | undefined> {
		for (const path of targets) {
			if (await underRecords(path)) return `Agent rails: ${path} is under the per-repo records root; agents never write records. ` +
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
			const result = await repoExec([...gate.pathCheck, ...changed], canonicalRoot, gate.pathCheckTimeoutMs ?? SUBPROCESS_TIMEOUT_MS);
			if (result.code !== 0 || result.killed) throw new Error(`${result.stdout}\n${result.stderr}`.trim());
			state.pathFailures.delete(root);
		} catch (error) {
			state.pathFailures.set(root, changed);
			return `Agent rails: pathCheck failed in ${root}: ${String(error)}`;
		}
	}
	// At the agent's commit in a dynaflows-on repo: the record's checks.commit (the repo's own
	// command), then the harness checks over the whole repo with the record as config. Any failure
	// or finding blocks the commit; there are no baselines and no diff scoping.
	async function commitChecksReason(root: string): Promise<string | undefined> {
		const record = await recordAt(root);
		if (!record) return;
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
		// A per-repo record file is harness config, not a change to the repo that holds it.
		if (runCheck) for (const [root, edited] of groups) {
			for (const path of edited) {
				if (await underRecords(path)) continue;
				await recordAuthor(ctx, root);
				break;
			}
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
	pi.on("before_subagent_spawn", async (event, ctx) => {
		if (event.agent !== "verifier") return;
		try {
			let scope = await scopeAt(ctx.cwd);
			let parent: string | undefined;
			const contextPath = resolve(scope.common, "omp-prove/verifier-context.json");
			const contextText = await readFile(contextPath, "utf8").catch(absentFile);
			try {
				if (contextText !== null) {
					const raw: unknown = JSON.parse(contextText);
					const context = z.object({ schemaVersion: z.literal(1), worktree: z.string(), branch: z.string().refine(value => value.startsWith("refs/heads/")), head: z.string().refine(value => /^[0-9a-f]{40}$/.test(value)), parent: z.string().regex(/^[0-9a-f]{40}$/) }).parse(raw);
					// SAFETY: the object schema above rejects null and primitive values before inspecting raw keys.
					if (Object.keys(raw as object).sort().join(",") !== "branch,head,parent,schemaVersion,worktree") throw new Error("unexpected verifier context fields");
					const source = await scopeAt(context.worktree);
					if (source.common !== scope.common || source.worktree !== context.worktree || source.branch !== context.branch ||
						await gitValue(source.worktree, ["rev-parse", "HEAD"]) !== context.head) throw new Error("scope mismatch");
					scope = source;
					parent = context.parent;
				}
			} catch (error) {
				throw new Error(`stale verifier context; rerun prove(): ${String(error)}`);
			}
			if (contextText === null && !(await dynaflowsOn(ctx, scope.worktree))) return;
			const authors = await authorsAt(scope, true, parent);
			const candidates = event.patterns.length
				? event.patterns.map(pattern => ({ pattern, model: ctx.models.resolve(pattern) }))
				: [{ pattern: undefined, model: ctx.models.current() }];
			if (candidates.some(candidate => !candidate.model)) throw new Error("cannot resolve every verifier model candidate");
			const independent = candidates.filter(candidate => candidate.model && !authors.some(author => author.family === ctx.models.family(candidate.model!)));
			if (!independent.length) throw new Error(`verifier must use a different model family from every recorded author: ${JSON.stringify(authors)}`);
			if (independent.length !== candidates.length) return {
				model: independent.flatMap(candidate => candidate.pattern === undefined ? [] : [candidate.pattern]),
				note: "Agent rails: removed verifier models from recorded author families.",
			};
		} catch (error) { return { block: true, reason: `Agent rails: ${String(error)}` }; }
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
					if (action.command.startsWith("gh ") && action.args[0] === "api") {
						if (action.args.some(arg => /(?:^|\/)labels(?:[/?#]|$)/.test(arg)) && action.args.some(arg => arg.includes(agentVerified))) {
							return { block: true, reason: agentVerifiedStatusOnly };
						}
						const reason = await statusPostReason(action, state, ctx);
						if (reason) return { block: true, reason };
					}
					if (/^gh (?:pr|issue) edit\b/.test(action.command) && action.args.some((arg, index, args) =>
						(arg === "--add-label" ? args[index + 1] ?? "" : arg.startsWith("--add-label=") ? arg.slice("--add-label=".length) : "")
							.split(",").includes(agentVerified))) {
						return { block: true, reason: agentVerifiedStatusOnly };
					}
					if (!new RegExp(map.gitCommand).test(action.command)) continue;
					const urls = missing(state, map, ["git"]);
					if (urls.length) return { block: true, reason: readReason(urls) };
					if (/^git (?:merge|rebase|cherry-pick|revert|am|pull)(?![-\w])/.test(action.command)) {
						const bypass = commitBypass(action);
						if (bypass) return { block: true, reason: `Agent rails: drop hook bypass ${bypass}` };
						if (!action.cwd) throw new Error("cannot resolve history operation cwd");
						const root = await repoRoot(action.cwd);
						if (root) await recordAuthor(ctx, root);
					}
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
							const sole = checkedCommitReason(event.input.command, action);
							if (sole) return { block: true, reason: sole };
							if (!commitOptions(action.args.slice(1)).all) {
								const diff = await pi.exec("git", ["diff", "--quiet"], { cwd: root, timeout: SUBPROCESS_TIMEOUT_MS });
								if (diff.code !== 0 || diff.killed) return {
									block: true, reason: diff.code === 1 && !diff.killed
										? "Agent rails: tracked files have unstaged changes, so the commit checks would not see what gets committed. Rule: without -a/--all, `git diff --quiet` must pass (every tracked change staged; untracked files are fine). Stage the changes (git add) or stash them, or commit with -a, then commit again."
										: `Agent rails: cannot check for unstaged changes (git diff --quiet): ${diff.stderr}`,
								};
							}
							const reason = await commitChecksReason(root);
							if (reason) return { block: true, reason };
						}
						if (root) await recordAuthor(ctx, root);
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
						const merge = /^gh pr merge\b/.test(action.command);
						const dynaflows = merge && await dynaflowsOn(ctx, action.cwd);
						if (merge && (gate?.requiredCi || dynaflows)) {
							const args = action.args;
							const at = args.findIndex(arg => arg === "--match-head-commit" || arg.startsWith("--match-head-commit="));
							const head = at < 0 ? undefined : args[at].includes("=") ? args[at].slice(args[at].indexOf("=") + 1) : args[at + 1];
							if (at < 0 || !head) return { block: true, reason: "Agent rails: merge requires --match-head-commit <40-hex verified SHA>" };
							if (/[$`]/.test(head)) return {
								block: true, reason: `Agent rails: received unexpanded shell text \`${head}\`; rails check the literal command, so pass the 40-hex SHA itself`,
							};
							if (!/^[0-9a-f]{40}$/.test(head)) return { block: true, reason: `Agent rails: --match-head-commit must be a lowercase 40-hex SHA, got ${head}` };
							if (dynaflows) {
								const reason = await passMarkerReason(action.cwd, head);
								if (reason) return { block: true, reason };
							}
							const pr = await prCi(action.cwd, prSelector(action.args), state, ctx);
							if (pr.head && pr.head !== head) return {
								block: true, reason: `Agent rails: PR head is ${pr.head}, not ${head}; re-verify the current head`,
							};
							if (pr.reason) return { block: true, reason: pr.reason };
							if (pr.agentVerified !== "SUCCESS") return {
								block: true, reason: `Agent rails: agent-verified status is ${pr.agentVerified ?? "missing"} for ${head}${pr.repository ? ` in ${pr.repository}` : ""}; verify this exact head first (prove() posts it; non-dynaflows: gh api repos/OWNER/REPO/statuses/SHA -f state=success -f context=agent-verified).`,
							};
						}
					}
				}
			}
			if (!map.editTools.includes(event.toolName)) {
				recordSnapshots.set(event.toolCallId, await recordEntries());
				return;
			}
			if (ctx.agent.kind !== "main") {
				const reason = await subagentPathReason([
					...pathArguments(event, map),
					...(event.input.path === map.resolveDevice ? state.proposal : []),
				]);
				if (reason) return { block: true, reason };
			}
			const targets = event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
			const recordWrite = await recordWriteReason(targets);
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
		try {
			const before = recordSnapshots.get(event.toolCallId);
			recordSnapshots.delete(event.toolCallId);
			const reverted = before ? await revertRecordChanges(before) : undefined;
			const result = await recordResult(event, ctx, stateFor(ctx), true);
			if (!reverted) return result;
			return { additionalContext: [reverted, result?.additionalContext].filter(Boolean).join("\n") };
		}
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
