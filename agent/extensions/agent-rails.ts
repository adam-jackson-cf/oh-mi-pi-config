import { readFileSync } from "node:fs";
import { isAbsolute, join, dirname } from "node:path";
import { type ExtensionAPI, type ExtensionContext, type SessionStartEvent, type SessionSwitchEvent, type SessionBranchEvent, type SessionTreeEvent } from "@oh-my-pi/pi-coding-agent";
import { fileURLToPath } from "node:url";
import { configRoot as defaultConfigRoot, parseOrigin, type Gate } from "../lib/repo-record.ts";
import {
	SUBPROCESS_TIMEOUT_MS, mappingSchema, detailsSchema, policyEvent, fresh, absolute, canonicalPath,
	worktreeTarget, repoRoot, pathArguments, subagentPathReason, missing, readReason, gitActions,
	commitBypass, readRecord, note, type GitAction, type Mapping, type State, type ToolEvent,
} from "../lib/rails-runtime.ts";

export default function agentRails(
	pi: ExtensionAPI,
	{
		configPath = fileURLToPath(new URL("../agent-rails.yml", import.meta.url)),
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
	async function worktreeReason(action: GitAction): Promise<string | undefined> {
		if (!action.cwd) throw new Error("cannot resolve git worktree add effective cwd");
		const validBase = (value: string | undefined): value is string =>
			value !== undefined && (isAbsolute(value) || value.startsWith("~/"));
		let base = process.env.OMP_WORKTREE_DIR;
		if (!validBase(base)) {
			if (!ompCommand.length) throw new Error("omp command is empty");
			const result = await pi.exec(ompCommand[0], [...ompCommand.slice(1), "config", "get", "worktree.base"],
				{ cwd: action.cwd, timeout: SUBPROCESS_TIMEOUT_MS });
			if (result.code !== 0 || result.killed) throw new Error(`cannot read worktree.base: ${result.stderr}`);
			base = result.stdout.trim();
		}
		if (!validBase(base)) return "Agent rails: worktree.base is unset; set it (agent/config.yml) before git worktree add.";
		const expandedBase = absolute(base, action.cwd);
		if (!expandedBase || !isAbsolute(expandedBase)) return "Agent rails: worktree.base is unset; set it (agent/config.yml) before git worktree add.";
		const remote = await pi.exec("git", ["remote", "get-url", "origin"], { cwd: action.cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (remote.killed) throw new Error("origin lookup was killed");
		if (remote.code !== 0) return "Agent rails: no origin remote; cannot create a worktree.";
		const origin = remote.stdout.trim();
		const slug = parseOrigin(origin);
		if (!slug) return `Agent rails: origin ${origin} is not a GitHub owner/name URL; cannot create a worktree.`;
		const name = slug.split("/")[1];
		const target = worktreeTarget(action.args);
		if (!target) throw new Error("git worktree add requires a target path");
		if (/[$`]/.test(target)) return `Agent rails: received unexpanded shell text \`${target}\`; rails check the literal command, so pass the worktree path itself`;
		const resolved = absolute(target, action.cwd);
		if (!resolved) throw new Error("git worktree add requires a filesystem target");
		if (dirname(canonicalPath(resolved)) !== canonicalPath(join(expandedBase, name))) {
			return `Agent rails: create worktrees at ${expandedBase}/${name}/<slug>; got ${resolved}.`;
		}
	}
	async function gateAt(root: string): Promise<Gate | undefined> {
		return (await readRecord(root, configRoot))?.gate;
	}
	function recordRead(event: ToolEvent, state: State): void {
		if (event.toolName !== "read" || event.isError || event.input.path === undefined) return;
		const url = event.input.path.replace(/:(?:raw|[\d,+-]+)(?::(?:raw|[\d,+-]+))*$/, "");
		if (url.startsWith("skill://")) state.reads.add(url);
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
					if (/^git worktree add\b/.test(action.command)) {
						const reason = await worktreeReason(action);
						if (reason) return { block: true, reason };
					}
					if (/^git commit\b/.test(action.command)) {
						const bypass = commitBypass(action);
						if (bypass) return {
							block: true, reason: `Agent rails: commits must run the pre-commit hook; drop ${bypass} and commit again.`,
						};
						if (!action.cwd) return {
							block: true, reason: "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git commit.",
						};
					}
					if (/^(?:git push\b|gh pr (?:create|merge)\b)/.test(action.command)) {
						if (!action.cwd) return {
							block: true, reason: "Agent rails: cannot resolve the command's effective cwd; use an explicit existing directory before git push or gh pr create/merge.",
						};
						const root = await repoRoot(action.cwd);
						if (!root) throw new Error("publishing requires a validated local Git repository");
						const gate = await gateAt(root);
						if (gate?.agentMerge === "disabled") return {
							block: true, reason: "Agent rails: agentMerge is disabled; ask the owner before git push, gh pr create or gh pr merge in this repository.",
						};
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
		} catch (error) { return { block: true, reason: `Agent rails: policy could not be evaluated: ${String(error)}` }; }
	});
	pi.on("tool_result", (runtimeEvent, ctx) => {
		if (loadError) return { additionalContext: loadError };
		recordResult(policyEvent(runtimeEvent), ctx, stateFor(ctx));
	});
	pi.registerCommand("extension-health-agent-rails", {
		description: "Agent rails registration marker",
		handler: async (_args, ctx) => { ctx.ui.notify(loadError ?? "Agent rails registered", loadError ? "error" : "info"); },
	});
}
