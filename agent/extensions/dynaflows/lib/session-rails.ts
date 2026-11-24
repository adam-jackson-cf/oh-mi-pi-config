// Session rails: end-of-turn obligations (structured mode), opt-in device rollout, review on
// restart and handoffs between sessions. index.ts wires them into the Git rails and the dynaflows tool.
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { gitActions, matches, policyEvent, repoRoot, SUBPROCESS_TIMEOUT_MS, type GitAction, type ToolEvent } from "./rails-runtime.ts";
import { OriginError, repoSlug, unresolvedText } from "./git-paths.ts";
import type { Mode } from "./modes.ts";
import { ObligationBook, ranCommand, shellJoin, type Obligation, type ObligationSource, type ObligationStatus, type Step } from "./obligations.ts";
import { consumeHandoff, readHandoff, stagedHandoffReason, writeHandoff } from "./handoff.ts";
import { defaultReviewsPath, extensionDiff, extensionRevision, storedRevision, storeRevision } from "./restart-review.ts";
import { prMerge } from "./guards.ts";
import { verdictText, type Approvals, type Decide, type Verdict } from "./judge.ts";
import { judgedRails, type Failure, type ImpactInput, type JudgedRails } from "./judged-rails.ts";

type SessionApi = Pick<ExtensionAPI, "on" | "exec" | "registerTool">;
type Run = { code: number; killed: boolean; stdout: string; stderr: string };
export type SessionDeps = {
	configRoot: string;
	extensionDir: string;
	reviewsPath: string;
	/** The real path of the Git common dir of the repository holding cwd (git-paths.ts). */
	commonDir(cwd: string): Promise<string>;
	modeOf(ctx: ExtensionContext, cwd: string): Promise<Mode>;
	/** The agent-rails config of the repository holding root when the engine accepted it (repo-config.ts); else undefined. */
	configOf(root: string): Promise<ImpactInput["config"]>;
	/** The turn-end status of a branch's PR, with the reason when it is unmet (CI pending or failing, gh unreadable). */
	prStatus(ctx: ExtensionContext, cwd: string, selector: string): Promise<PrStatus>;
	/** Whether the branch has an open PR (gh pr view), for PRs this session did not create. */
	prOpen(ctx: ExtensionContext, cwd: string, branch: string): Promise<boolean>;
	repoRun(argv: string[], cwd: string, timeout: number): Promise<Run>;
	checks: { config: string[]; check: string[] };
	/** The judged rails' decide(): the judgment registry with the host judge transport. */
	decide: Decide;
	/** The owner's approvals, shared with the Git rails' autonomy asks. */
	approvals: Approvals;
	/** Whether one command part needs in-scope before it runs (rails.yml askFirstCommand). */
	askFirst(part: string): boolean;
	sources: ObligationSource[];
	loadError(): string | undefined;
};
export type PrStatus = { status: ObligationStatus; reason?: string | undefined };
type Review = { slug: string; root: string; common: string; from: string; to: string; checks: Promise<string>; diff: Promise<string | undefined>; impact: Promise<Verdict | Failure> };
type Handoff = { root: string; content: string; injected: boolean };
// reviewError: the origin remote could not be read at session start, so the extension review is unknown and Git writes refuse.
type Session = { book: ObligationBook; review?: Review | undefined; reviewError?: string | undefined; handoff?: Handoff | undefined; started: boolean; discovered: Set<string> };
// Bounded under omp's 30 s extension handler budget; a slower check is left for the agent to run.
const REVIEW_CHECK_MS = 25_000;
const OUTPUT_TAIL = 2000;
const ROLLOUT_DIRECTIVE = /^\$rollout$/i;
const ROLLOUT_ID = "rollout";
const branchEntrySchema = z.object({ type: z.literal("message"), message: z.object({
	role: z.string(),
	content: z.union([z.string(), z.array(z.object({ type: z.string(), text: z.string().optional(), id: z.string().optional(), name: z.string().optional(), arguments: z.unknown().optional() }))]).optional(),
	toolCallId: z.string().optional(), isError: z.boolean().optional(),
}) });
const toolArgumentsSchema = z.object({ command: z.string().optional(), cwd: z.string().optional(), rollout: z.literal(true).optional() }).catch({});
type BranchMessage = z.infer<typeof branchEntrySchema>["message"];
type ReplayCall = { name: string; input: z.infer<typeof toolArgumentsSchema> };
const messageTexts = (message: BranchMessage): string[] =>
	Array.isArray(message.content) ? message.content.map(block => block.text ?? "") : [message.content ?? ""];
function toolCalls(message: BranchMessage): [string, ReplayCall][] {
	const calls: [string, ReplayCall][] = [];
	for (const block of Array.isArray(message.content) ? message.content : []) {
		if (block.type === "toolCall" && block.id && block.name) calls.push([block.id, { name: block.name, input: toolArgumentsSchema.parse(block.arguments) }]);
	}
	return calls;
}
const asksRollout = (message: BranchMessage): boolean =>
	message.role === "user" && messageTexts(message).some(text => text.split("\n").some(line => ROLLOUT_DIRECTIVE.test(line.trim())));
const filesSchema = z.object({ files: z.array(z.object({ path: z.string() })) });
const agentMessage = (content: string) => ({ message: { customType: "dynaflows", content, display: false, attribution: "agent" as const } });
const sentenceOf = (text: string) => /[.!?]$/.test(text.trim()) ? text.trim() : `${text.trim()}.`;
const tail = (text: string) => text.length > OUTPUT_TAIL ? `…${text.slice(-OUTPUT_TAIL)}` : text;

async function reviewTask(review: Review): Promise<string | undefined> {
	const [checks, diff, impact] = await Promise.all([review.checks, review.diff, review.impact]);
	// Only an enforced "no" skips the review task; a shadow verdict never changes what the rail does.
	if (!("failure" in impact) && impact.mode === "enforced" && impact.outcome === "no") return undefined;
	const judged = "failure" in impact ? `not judged: ${impact.failure}` : verdictText(impact);
	return `First task: the dynaflows extension changed (${review.from} → ${review.to}) since ${review.slug} last acknowledged it. ` +
		"Review the change and address anything that affects this repository's setup or its interactions with the rails first; " +
		"Git writes are refused until you call the dynaflows tool with acknowledge: true.\n" +
		`Extension impact: ${judged}\nExtension diff:\n${diff ?? "unavailable (not a Git revision pair)"}\nConfig checks:\n${checks}`;
}

export type SessionRails = {
	gitWriteReason(action: GitAction, ctx: ExtensionContext): Promise<string | undefined>;
	commitReason(action: GitAction): Promise<string | undefined>;
	optInRollout(ctx: ExtensionContext): Promise<string>;
	acknowledge(ctx: ExtensionContext): Promise<string>;
	addSource(source: ObligationSource): void;
	judged: JudgedRails;
};

export function sessionRails(pi: SessionApi, deps: SessionDeps): SessionRails {
	const sessions = new Map<string, Session>();
	const sessionFor = (ctx: ExtensionContext): Session => {
		const id = ctx.sessionManager.getSessionId();
		const found = sessions.get(id) ?? { book: new ObligationBook(), started: false, discovered: new Set<string>() };
		sessions.set(id, found);
		return found;
	};
	const structured = async (ctx: ExtensionContext, root: string) => await deps.modeOf(ctx, root) === "structured";
	// For judgments on actions that write nothing through git or gh: an unresolvable mode is notified and the judgment does not run.
	const structuredOrNotify = async (ctx: ExtensionContext, root: string) => {
		try { return await structured(ctx, root); }
		catch (error) {
			if (!(error instanceof OriginError)) throw error;
			ctx.ui.notify(unresolvedText(error), "error");
			return false;
		}
	};
	// The rollout step, from the config root only: any spelling that resolves to the same script counts.
	const rolloutStep: Step = { argv: ["python3", `${deps.configRoot.replace(/\/+$/, "")}/scripts/device_rollout.py`], root: deps.configRoot };
	const rolloutCommand = shellJoin(rolloutStep.argv);
	const judged = judgedRails(pi, { decide: deps.decide, approvals: deps.approvals, structured: structuredOrNotify, askFirst: deps.askFirst });
	// The repository's config when the engine accepted it; any other state, or a failure to resolve it, has none to read.
	const configOf = (root: string) => deps.configOf(root).catch(() => undefined);

	async function reviewChecks(root: string): Promise<string> {
		if (!await configOf(root)) return "no valid .agentrails/config.json; review the config against the change by hand.";
		const lines: string[] = [];
		for (const [label, prefix] of [["config-check", deps.checks.config], ["check", deps.checks.check]] as const) {
			const argv = [...prefix, "--repo", root];
			const result = await deps.repoRun(argv, root, REVIEW_CHECK_MS);
			const outcome = result.killed ? `did not finish in ${REVIEW_CHECK_MS / 1000} s; run ${shellJoin(argv)}` : `exit ${result.code}`;
			lines.push(`guardrails ${label}: ${outcome}${result.code === 0 ? "" : `\n${tail(`${result.stdout}\n${result.stderr}`.trim())}`}`);
		}
		return lines.join("\n");
	}

	async function startReview(ctx: ExtensionContext, root: string, session: Session): Promise<void> {
		const slug = await repoSlug(root);
		if (!slug) return;
		const to = await extensionRevision(pi, deps.configRoot, deps.extensionDir);
		const from = await storedRevision(deps.reviewsPath, slug);
		if (from === undefined) { await storeRevision(deps.reviewsPath, slug, to); return; }
		if (from === to) return;
		if (!await structured(ctx, root)) {
			ctx.ui.notify(`Agent rails: the dynaflows extension changed (${from} → ${to}) since ${slug} last acknowledged it; review the change.`, "info");
			await storeRevision(deps.reviewsPath, slug, to);
			return;
		}
		const config = await configOf(root);
		session.review = {
			slug, root, common: await deps.commonDir(root), from, to, checks: reviewChecks(root), diff: extensionDiff(pi, deps.configRoot, deps.extensionDir, { from, to }),
			impact: judged.impactVerdict(ctx, { slug, config, configRoot: deps.configRoot, from, to }),
		};
	}

	pi.on("session_start", async (_event, ctx) => {
		if (deps.loadError() || ctx.agent.kind !== "main") return;
		const session = sessionFor(ctx);
		await replay(ctx, session);
		const root = await repoRoot(ctx.cwd);
		if (!root) return;
		const content = await readHandoff(root);
		if (content !== null) session.handoff = { root, content, injected: false };
		session.reviewError = undefined;
		await startReview(ctx, root, session).catch((error: Error) => {
			if (!(error instanceof OriginError)) throw error;
			session.reviewError = error.message;
			ctx.ui.notify(`Agent rails: could not check the dynaflows extension review for ${root} (${error.message}); Git writes are refused until the owner restarts omp.`, "error");
		});
	});

	async function firstPromptMessages(session: Session, ctx: ExtensionContext): Promise<string[]> {
		const parts: string[] = [];
		if (session.review) {
			const task = await reviewTask(session.review);
			if (task) parts.push(task);
			else {
				const { slug, from, to } = session.review;
				const impact = await session.review.impact;
				// A "no" skips the review task but stays visible: the owner sees it and the revision is stored.
				ctx.ui.notify(`Agent rails: the dynaflows extension changed (${from} → ${to}); ${"failure" in impact ? "extension-impact did not run" : verdictText(impact)} for ${slug}, so no review task; revision stored.`, "info");
				await storeRevision(deps.reviewsPath, slug, to);
				session.review = undefined;
			}
		}
		if (session.handoff && !session.handoff.injected) {
			session.handoff.injected = true;
			parts.push(`Handoff from an earlier session (${session.handoff.root}/HANDOFF.md; deleted when this turn ends):\n${session.handoff.content}`);
		}
		return parts;
	}

	pi.on("session_switch", async (_event, ctx) => {
		if (deps.loadError() || ctx.agent.kind !== "main") return;
		await replay(ctx, sessionFor(ctx));
	});

	pi.on("before_agent_start", async (event, ctx) => {
		if (deps.loadError() || ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const session = sessionFor(ctx);
		const parts = session.started ? [] : await firstPromptMessages(session, ctx);
		session.started = true;
		if ((event.prompt ?? "").split("\n").some(line => ROLLOUT_DIRECTIVE.test(line.trim()))) parts.push(await rails.optInRollout(ctx));
		return parts.length ? agentMessage(parts.join("\n\n")) : undefined;
	});

	pi.on("agent_end", async (event, ctx) => {
		const session = sessionFor(ctx);
		if (event.willContinue || !session.handoff?.injected) return;
		await consumeHandoff(session.handoff.root, session.handoff.content);
		session.handoff = undefined;
	});

	async function currentBranch(cwd: string): Promise<string> {
		return (await pi.exec("git", ["symbolic-ref", "--short", "-q", "HEAD"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS })).stdout.trim();
	}

	function trackBranch(ctx: ExtensionContext, session: Session, at: { cwd: string; root: string; branch: string }): void {
		const { cwd, root, branch } = at;
		session.discovered.add(`${root}:${branch}`);
		const merged = `the PR for ${branch} has green CI on its head and is unmerged; merge it (skill://git-tool/references/merge-and-recover.md)`;
		const obligation: Obligation = { id: `pr:${branch}`, root, text: merged, status: async () => {
			const pr = await deps.prStatus(ctx, cwd, branch);
			obligation.text = pr.reason === undefined ? merged
				: `the PR for ${branch} is open and not mergeable yet: ${sentenceOf(pr.reason.replace(/^Agent rails: /, ""))} Wait for CI with that command (or gh pr checks ${branch} --watch), fix any failure, then merge`;
			return pr.status;
		} };
		session.book.add(obligation);
		session.book.add(judged.evidenceObligation(ctx, { cwd, root, branch,
			requiredContext: async () => {
				const ci = (await configOf(root))?.ci;
				return ci?.enabled ? ci.requiredContext : undefined;
			} }));
	}

	async function trackPr(action: GitAction, ctx: ExtensionContext, session: Session): Promise<void> {
		if (!action.cwd) return;
		const root = await repoRoot(action.cwd);
		if (!root) return;
		const flag = action.args.findIndex(arg => arg === "--head" || arg === "-H");
		const branch = flag >= 0 ? action.args[flag + 1] : await currentBranch(action.cwd);
		if (branch) trackBranch(ctx, session, { cwd: action.cwd, root, branch });
	}

	// A PR this session did not open (earlier session, gh api, another tool): the current branch's open PR, once pushed.
	async function discoverPr(ctx: ExtensionContext, session: Session): Promise<void> {
		if (ctx.agent.kind !== "main") return;
		const root = await repoRoot(ctx.cwd);
		if (!root || !await structured(ctx, root)) return;
		const branch = await currentBranch(ctx.cwd);
		if (!branch || session.discovered.has(`${root}:${branch}`)) return;
		const pushed = await pi.exec("git", ["rev-parse", "--verify", "-q", `refs/remotes/origin/${branch}`], { cwd: ctx.cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (pushed.code !== 0 || !await deps.prOpen(ctx, ctx.cwd, branch)) return;
		trackBranch(ctx, session, { cwd: ctx.cwd, root, branch });
	}

	async function afterMergeStep(root: string | undefined) {
		return root ? (await configOf(root))?.afterMerge : undefined;
	}

	// A merge needs the step unless the config names paths and the PR's files (when gh lists them) match none.
	async function mergeMatches(action: GitAction, cwd: string, paths: string[] | undefined): Promise<boolean> {
		if (!paths) return true;
		const selector = prMerge(action.args).selectors[0];
		const view = await pi.exec("gh", ["pr", "view", ...(selector ? [selector] : []), "--json", "files"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (view.code !== 0) return true;
		return filesSchema.parse(JSON.parse(view.stdout)).files.some(item => matches(item.path, paths));
	}

	async function requireAfterMerge(action: GitAction, session: Session): Promise<void> {
		const root = action.cwd ? await repoRoot(action.cwd) : undefined;
		const step = await afterMergeStep(root);
		if (!root || !step || !action.cwd || !await mergeMatches(action, action.cwd, step.paths)) return;
		const command = shellJoin(step.argv);
		session.book.add({ id: `afterMerge:${root}`, root, text: `the config's afterMerge step has not exited 0 since the merge; run \`${command}\` in ${root}`,
			status: async () => "unmet" });
	}

	async function satisfied(event: ToolEvent, cwd: string, session: Session): Promise<void> {
		const command = event.input.command ?? "";
		if (ranCommand(command, rolloutStep, cwd)) session.book.satisfy(ROLLOUT_ID);
		const root = await repoRoot(cwd);
		const step = await afterMergeStep(root);
		if (root && step && ranCommand(command, { argv: step.argv, root }, cwd)) session.book.satisfy(`afterMerge:${root}`);
	}

	async function bashResult(event: ToolEvent, ctx: ExtensionContext, session: Session): Promise<void> {
		if (event.isError || event.input.command === undefined) return;
		const cwd = event.input.cwd ?? ctx.cwd;
		await satisfied(event, cwd, session);
		for (const action of await gitActions(event.input.command, cwd).catch(() => [])) {
			if (/^gh pr create\b/.test(action.command)) await trackPr(action, ctx, session);
			if (/^gh pr merge\b/.test(action.command)) await requireAfterMerge(action, session);
		}
	}

	async function addRollout(ctx: ExtensionContext, session: Session): Promise<string> {
		const root = await repoRoot(ctx.cwd);
		const applies = root ? await structured(ctx, root).catch((error: Error) => {
			if (error instanceof OriginError) return error;
			throw error;
		}) : false;
		if (applies instanceof OriginError) return `${unresolvedText(applies)} No device rollout obligation was added.`;
		if (!root || !applies) return "Device rollout obligations need dynaflows structured mode; none was added.";
		session.book.add({ id: ROLLOUT_ID, root, text: `device rollout was requested; run \`${rolloutCommand}\` until it exits 0`, status: async () => "unmet" });
		return `Device rollout requested: before this work ends, run \`${rolloutCommand}\` until it exits 0.`;
	}

	async function replayResult(call: ReplayCall, id: string, ctx: ExtensionContext, session: Session): Promise<void> {
		if (call.name === "bash") await bashResult(policyEvent({ type: "tool_call", toolName: "bash", toolCallId: id, input: call.input }), ctx, session);
		if (call.name === "dynaflows" && call.input.rollout) await addRollout(ctx, session);
	}

	async function replayMessage(message: BranchMessage, calls: Map<string, ReplayCall>, ctx: ExtensionContext, session: Session): Promise<void> {
		if (asksRollout(message)) await addRollout(ctx, session);
		if (message.role === "assistant") for (const [id, call] of toolCalls(message)) calls.set(id, call);
		const id = message.toolCallId ?? "";
		const call = message.role === "toolResult" && !message.isError ? calls.get(id) : undefined;
		if (call) await replayResult(call, id, ctx, session);
	}

	// Obligations live in memory: a restarted or resumed session rebuilds them from its branch, in order.
	async function replay(ctx: ExtensionContext, session: Session): Promise<void> {
		session.book = new ObligationBook();
		session.discovered.clear();
		const calls = new Map<string, ReplayCall>();
		for (const entry of ctx.sessionManager.getBranch()) {
			const parsed = branchEntrySchema.safeParse(entry);
			if (parsed.success) await replayMessage(parsed.data.message, calls, ctx, session);
		}
	}

	pi.on("tool_result", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (deps.loadError() || event.toolName !== "bash") return;
		await bashResult(event, ctx, sessionFor(ctx));
	});

	pi.on("session_stop", async (_event, ctx) => {
		if (deps.loadError()) return;
		const session = sessionFor(ctx);
		try {
			await discoverPr(ctx, session);
			const extra = (await Promise.all(deps.sources.map(source => source(ctx.sessionManager.getSessionId())))).flat();
			const reason = await session.book.turnEnd(extra, root => structured(ctx, root), text => ctx.ui.notify(text, "error"));
			return reason ? { decision: "block" as const, reason } : undefined;
		} catch (error) {
			// Obligations of a repository whose mode is unknown are not dropped: the work cannot end yet.
			if (error instanceof OriginError) return { decision: "block" as const, reason: unresolvedText(error) };
			throw error;
		}
	});

	const handoffParams = z.object({ content: z.string().min(1).describe("The handoff: state, decisions, next steps") });
	pi.registerTool({
		name: "handoff",
		label: "Handoff",
		description: "Write a handoff for the next session in this worktree: <worktree root>/HANDOFF.md, excluded from Git. " +
			"The next session here receives it as context and deletes it after its first turn.",
		parameters: handoffParams,
		approval: "write",
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const root = await repoRoot(ctx.cwd);
			const text = root ? `Agent rails: handoff written to ${await writeHandoff(pi, root, handoffParams.parse(params).content)}.` : "Agent rails: handoff needs a Git worktree.";
			return { content: [{ type: "text" as const, text }], details: {} };
		},
	});

	const rails: SessionRails = {
		async gitWriteReason(action, ctx) {
			const { review, reviewError } = sessionFor(ctx);
			if (reviewError) return `Agent rails: could not check the dynaflows extension review at session start (${reviewError}); the owner restarts omp.`;
			if (!review || !action.cwd || await deps.commonDir(action.cwd).catch(() => undefined) !== review.common) return undefined;
			return `Agent rails: the dynaflows extension changed (${review.from} → ${review.to}) since ${review.slug} last acknowledged it; ` +
				"review the change, then call the dynaflows tool with acknowledge: true before Git writes.";
		},
		async commitReason(action) {
			return action.cwd ? stagedHandoffReason(pi, action.cwd) : undefined;
		},
		async optInRollout(ctx) {
			return addRollout(ctx, sessionFor(ctx));
		},
		async acknowledge(ctx) {
			const session = sessionFor(ctx);
			if (!session.review) return "Agent rails: no extension change is waiting for acknowledgement.";
			await storeRevision(deps.reviewsPath, session.review.slug, session.review.to);
			const done = `Agent rails: ${session.review.slug} acknowledged dynaflows extension revision ${session.review.to}.`;
			session.review = undefined;
			return done;
		},
		addSource(source) { deps.sources.push(source); },
		judged,
	};
	return rails;
}

export type { Obligation, ObligationSource, ObligationStatus };
export { Approvals, ompJudge, registryDecide, type JudgeTransport } from "./judge.ts";
export { defaultReviewsPath };
export { scopeInputSchema } from "./judged-rails.ts";
