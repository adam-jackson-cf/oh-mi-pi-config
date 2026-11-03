import { access, readFile, stat } from "node:fs/promises";
import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { dirname, relative, resolve } from "node:path";
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
	agentMerge: z.enum(["allowed", "disabled"]).optional(),
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
const detailsSchema = z.object({
	exitCode: z.number().optional().catch(undefined),
	applied: z.boolean().optional().catch(undefined),
	files: stringList.optional().catch(undefined),
}).catch({ exitCode: undefined, applied: undefined, files: undefined });
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
	revision: number;
	discovered: boolean;
	pointed: boolean;
	proposal: string[];
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
function paths(event: ToolEvent, map: Mapping, cwd: string): string[] {
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
		absolute(value.trim().replace(/:[\d,+-]+(?::raw)?$/, ""), cwd),
	).filter((value): value is string => value !== undefined))];
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

// Parse command positions, not quoted prose/arguments. This is intentionally not a shell evaluator.
function gitActions(command: string, cwd: string): { command: string; cwd: string }[] {
	const tokens = command.match(/(?:[^\s;&|()"'\\]+|\\.|"(?:\\.|[^"\\])*"|'[^']*')+|&&|\|\||[;&|()\n]/g) ?? [];
	const actions: { command: string; cwd: string }[] = [];
	let words: string[] = [];
	const flush = () => {
		while (/^[A-Za-z_]\w*=/.test(words[0] ?? "")) words.shift();
		if (words[0] === "gh") actions.push({ command: words.join(" "), cwd });
		if (words.shift() === "git") {
			let effective = cwd;
			while (words[0]?.startsWith("-")) {
				const option = words.shift()!;
				if (["-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"].includes(option)) {
					const value = words.shift();
					if (option === "-C" && value) effective = resolve(effective, value);
				} else if (option.startsWith("-C") && option.length > 2) effective = resolve(effective, option.slice(2));
			}
			actions.push({ command: `git ${words.join(" ")}`, cwd: effective });
		}
		words = [];
	};
	for (const token of tokens) {
		if (/^(?:&&|\|\||[;&|()\n])$/.test(token)) flush();
		else words.push(token.replace(/"((?:\\.|[^"\\])*)"|'([^']*)'|\\(.)/g,
			(_match, double: string | undefined, single: string | undefined, escaped: string | undefined) =>
				double?.replace(/\\(["\\$`])/g, "$1") ?? single ?? escaped ?? ""));
	}
	flush();
	return actions;
}

export default function skillGate(pi: ExtensionAPI): void {
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
	async function fingerprint(root: string): Promise<string | undefined> {
		const options = { cwd: root, timeout: 20000 };
		const status = await pi.exec("git", ["status", "--porcelain=v1", "-z"], options);
		if (status.code !== 0 || status.killed) return;
		const diff = await pi.exec("git", ["diff", "HEAD", "--binary"], options);
		if (diff.code !== 0 || diff.killed) throw new Error(`cannot fingerprint ${root}: ${diff.stderr}`);
		return createHash("sha256").update(status.stdout).update("\0").update(diff.stdout).digest("hex");
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
			if (started && details.exitCode !== undefined) {
				const gate = await gateAt(started.root, state, ctx);
				if (gate?.fastGate?.join(" ") === event.input.command) {
					state.passed.set(started.root, started.revision);
					if (runCheck && started.fingerprint !== undefined) state.checkedTrees.set(started.root, started.fingerprint);
				}
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
				for (const action of gitActions(event.input.command, cwd)) {
					if (!new RegExp(map.gitCommand).test(action.command)) continue;
					const urls = missing(state, map, ["git"]);
					if (urls.length) return { block: true, reason: readReason(urls) };
					if (/^(?:git push\b|gh pr (?:create|merge)\b)/.test(action.command)) {
						const root = await repoRoot(action.cwd);
						const gate = root ? await gateAt(root, state, ctx, true) : undefined;
						if (gate?.agentMerge === "disabled") return {
							block: true, reason: "Skill gate: agentMerge is disabled; ask the owner for approval before branch/push/PR/merge actions in this repository.",
						};
					}
				}
			}
			if (!map.editTools.includes(event.toolName)) return;
			const targets = event.input.path === map.resolveDevice ? state.proposal : paths(event, map, ctx.cwd);
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
			const reasons: string[] = [];
			let changedTree = false;
			const roots = new Set([...state.edits.keys(), ...state.pathFailures.keys(), ...state.baselines.keys()]);
			for (const root of roots) {
				const gate = await gateAt(root, state, ctx);
				const failedPaths = state.pathFailures.get(root);
				if (failedPaths && gate?.pathCheck) {
					const error = await checkPaths(root, failedPaths, gate, state);
					if (error) reasons.push(error);
				}
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
	pi.on("before_agent_start", (event, ctx) => {
		const state = stateFor(ctx);
		if (loadError || ctx.agent.kind !== "main" || event.reason !== "prompt" || state.pointed) return;
		state.pointed = true;
		return { message: {
			customType: "skill-gate-workflow", content: `Route this work with ${map.references.workflow.join(" and ")}.`,
			display: false, attribution: "agent",
		} };
	});
	pi.registerCommand("extension-health-skill-gate", {
		description: "Skill gate registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Skill gate registered", loadError ? "error" : "info"); },
	});
}
