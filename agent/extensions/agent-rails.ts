import { readFileSync } from "node:fs";
import { isAbsolute, join, dirname } from "node:path";
import { type ExtensionAPI, type ExtensionContext, type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent } from "@oh-my-pi/pi-coding-agent";
import { fileURLToPath } from "node:url";
import { configRoot as defaultConfigRoot, parseOrigin, type Gate } from "../lib/repo-record.ts";
import {
	SUBPROCESS_TIMEOUT_MS, mappingSchema, detailsSchema, policyEvent, fresh, absolute, canonicalPath,
	worktreeTarget, repoRoot, pathArguments, subagentPathReason, missing, readReason, gitActions,
	commitBypass, readRecord, note, recordRead, type GitAction, type Mapping, type State, type ToolEvent,
} from "../lib/rails-runtime.ts";

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

function commitReason(action: GitAction): string | undefined {
	const bypass = commitBypass(action);
	if (bypass) return `Agent rails: commits must run the pre-commit hook; drop ${bypass} and commit again.`;
	if (!action.cwd) return "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.";
	return undefined;
}

const PULLS_COMMITS = /^git (?:merge|rebase|cherry-pick|revert|am|pull)(?![-\w])/;
const PUBLISHES = /^(?:git push\b|gh pr (?:create|merge)\b)/;

export default function agentRails(
	pi: ExtensionAPI,
	{
		configPath = fileURLToPath(new URL("../rails.yml", import.meta.url)),
		configRoot = defaultConfigRoot(),
		ompCommand = ["omp"],
	}: { configPath?: string; configRoot?: string; ompCommand?: string[] } = {},
): void {
	const states = new Map<string, State>();
	let map: Mapping;
	let loadError: string | undefined;
	try {
		const parsed = mappingSchema.safeParse(Bun.YAML.parse(readFileSync(configPath, "utf8")));
		if (!parsed.success) throw new Error(`invalid mappings: ${parsed.error.message}`);
		map = parsed.data;
	} catch (error) {
		loadError = `Agent rails: cannot load rails.yml: ${String(error)}`;
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
	async function configuredBase(cwd: string): Promise<string | undefined> {
		const environmentBase = process.env["OMP_WORKTREE_DIR"];
		if (validBase(environmentBase)) return environmentBase;
		const [command, ...prefix] = ompCommand;
		if (command === undefined) throw new Error("omp command is empty");
		const result = await pi.exec(command, [...prefix, "config", "get", "worktree.base"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code !== 0 || result.killed) throw new Error(`cannot read worktree.base: ${result.stderr}`);
		return result.stdout.trim();
	}
	async function originUrl(cwd: string): Promise<string | undefined> {
		const remote = await pi.exec("git", ["remote", "get-url", "origin"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (remote.killed) throw new Error("origin lookup was killed");
		return remote.code === 0 ? remote.stdout.trim() : undefined;
	}
	async function worktreeReason(action: GitAction): Promise<string | undefined> {
		if (!action.cwd) throw new Error("cannot resolve git worktree add effective cwd");
		const base = await configuredBase(action.cwd);
		const expandedBase = validBase(base) ? absolute(base, action.cwd) : undefined;
		if (!expandedBase || !isAbsolute(expandedBase)) return "Agent rails: worktree.base is unset; set it (agent/config.yml) before git worktree add.";
		const origin = await originUrl(action.cwd);
		if (origin === undefined) return "Agent rails: no origin remote; cannot create a worktree.";
		const name = parseOrigin(origin)?.split("/")[1];
		if (!name) return `Agent rails: origin ${origin} is not a GitHub owner/name URL; cannot create a worktree.`;
		return worktreeTargetReason(action.args, action.cwd, expandedBase, name);
	}
	async function gateAt(root: string): Promise<Gate | undefined> {
		return (await readRecord(root, configRoot))?.gate;
	}
	function recordResult(event: ToolEvent, ctx: ExtensionContext, state: State): void {
		recordRead(event, state);
		if (event.isError) return;
		if (event.toolName === "ast_edit" && event.details.applied !== true) {
			state.proposal = event.details.files?.flatMap(path => absolute(path, ctx.cwd) ?? []) ?? [];
		} else if (event.toolName === "write" && [map.resolveDevice, map.rejectDevice].includes(event.input.path ?? "")) {
			state.proposal = [];
		}
	}
	async function rebuild(_event: SessionStartEvent | SessionSwitchEvent | SessionBranchEvent | SessionTreeEvent, ctx: ExtensionContext): Promise<void> {
		const state = fresh();
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
				if (call) recordResult({ ...call, isError: message.isError, details: detailsSchema.parse(message.details) }, ctx, state);
			}
		}
	}
	pi.on("session_start", rebuild);
	pi.on("session_switch", rebuild);
	pi.on("session_branch", rebuild);
	pi.on("session_tree", rebuild);
	pi.on("session_compact", (_event, ctx) => {
		const state = stateFor(ctx);
		state.reads.clear();
	});
	async function publishReason(action: GitAction): Promise<string | undefined> {
		if (!action.cwd) return "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push or gh pr create/merge.";
		const root = await repoRoot(action.cwd);
		if (!root) throw new Error("publishing requires a validated local Git repository");
		const gate = await gateAt(root);
		if (gate?.agentMerge === "disabled") return "Agent rails: agentMerge is disabled; ask the owner before git push, gh pr create or gh pr merge in this repository.";
		return undefined;
	}
	async function gatedActionReason(action: GitAction, state: State): Promise<string | undefined> {
		const urls = missing(state, map, ["git"]);
		if (urls.length) return readReason(urls);
		const worktree = /^git worktree add\b/.test(action.command) ? await worktreeReason(action) : undefined;
		if (worktree) return worktree;
		const commit = /^git commit\b/.test(action.command) ? commitReason(action) : undefined;
		if (commit) return commit;
		return PUBLISHES.test(action.command) ? publishReason(action) : undefined;
	}
	async function actionReason(action: GitAction, state: State): Promise<string | undefined> {
		if (action.hookSkip) return `Agent rails: pre-commit hook environment mutation is forbidden: ${action.hookSkip}`;
		if (PULLS_COMMITS.test(action.command)) {
			const bypass = commitBypass(action);
			return bypass ? `Agent rails: drop hook bypass ${bypass}` : undefined;
		}
		if (!new RegExp(map.gitCommand).test(action.command)) return undefined;
		return gatedActionReason(action, state);
	}
	async function bashReason(command: string, cwd: string, state: State): Promise<string | undefined> {
		for (const action of await gitActions(command, cwd)) {
			const reason = await actionReason(action, state);
			if (reason) return reason;
		}
		return undefined;
	}
	async function toolCallReason(event: ToolEvent, ctx: ExtensionContext, state: State): Promise<string | undefined> {
		if (event.toolName === "bash" && event.input.command !== undefined) {
			const reason = await bashReason(event.input.command, absolute(event.input.cwd ?? ctx.cwd, ctx.cwd) ?? ctx.cwd, state);
			if (reason) return reason;
		}
		if (!map.editTools.includes(event.toolName) || ctx.agent.kind === "main") return undefined;
		return subagentPathReason([
			...pathArguments(event, map),
			...(event.input.path === map.resolveDevice ? state.proposal : []),
		]);
	}
	pi.on("tool_call", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (loadError) return { block: true, reason: loadError };
		const state = stateFor(ctx);
		try {
			const reason = await toolCallReason(event, ctx, state);
			return reason ? { block: true, reason } : undefined;
		} catch (error) { return { block: true, reason: `Agent rails: policy could not be evaluated: ${String(error)}` }; }
	});
	pi.on("tool_result", (runtimeEvent, ctx) => {
		if (loadError) return { additionalContext: loadError };
		recordResult(policyEvent(runtimeEvent), ctx, stateFor(ctx));
		return undefined;
	});
	pi.registerCommand("extension-health-agent-rails", {
		description: "Agent rails registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Agent rails registered", loadError ? "error" : "info"); },
	});
}
