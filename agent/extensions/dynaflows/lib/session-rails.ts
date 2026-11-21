// Session rails: end-of-turn obligations (structured mode), opt-in device rollout, review on
// restart and handoffs between sessions. index.ts wires them into the Git rails and the dynaflows tool.
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { gitActions, matches, policyEvent, repoRoot, SUBPROCESS_TIMEOUT_MS, type GitAction, type RepoRecord, type ToolEvent } from "./rails-runtime.ts";
import { repoSlug } from "./repo-record.ts";
import type { Mode } from "./modes.ts";
import { ObligationBook, ranCommand, shellJoin, type Obligation, type ObligationSource, type ObligationStatus } from "./obligations.ts";
import { consumeHandoff, readHandoff, stagedHandoffReason, writeHandoff } from "./handoff.ts";
import { extensionDiff, extensionRevision, storedRevision, storeRevision } from "./restart-review.ts";
import { prSelector } from "./guards.ts";
import { verdictText, type Decide, type Verdict } from "./judge.ts";
import { judgedRails, type Failure, type JudgedRails } from "./judged-rails.ts";

type SessionApi = Pick<ExtensionAPI, "on" | "exec" | "registerTool">;
type Run = { code: number; killed: boolean; stdout: string; stderr: string };
export type SessionDeps = {
	configRoot: string;
	extensionDir: string;
	reviewsPath: string;
	modeOf(ctx: ExtensionContext, cwd: string): Promise<Mode>;
	recordAt(root: string): Promise<RepoRecord | undefined>;
	prStatus(ctx: ExtensionContext, cwd: string, selector: string): Promise<ObligationStatus>;
	repoRun(argv: string[], cwd: string, timeout: number): Promise<Run>;
	checks: { profile: string[]; check: string[] };
	/** The judged rails' decide(): the judgment registry with the host judge transport. */
	decide: Decide;
	/** Whether one command part needs in-scope before it runs (rails.yml askFirstCommand). */
	askFirst(part: string): boolean;
	sources: ObligationSource[];
	loadError(): string | undefined;
};
type Review = { slug: string; root: string; from: string; to: string; checks: Promise<string>; diff: Promise<string | undefined>; impact: Promise<Verdict | Failure> };
type Handoff = { root: string; content: string; injected: boolean };
type Session = { book: ObligationBook; review?: Review | undefined; handoff?: Handoff | undefined; started: boolean };
// Bounded under omp's 30 s extension handler budget; a slower check is left for the agent to run.
const REVIEW_CHECK_MS = 25_000;
const OUTPUT_TAIL = 2000;
const ROLLOUT_DIRECTIVE = /^\$rollout$/i;
const ROLLOUT_ID = "rollout";
const filesSchema = z.object({ files: z.array(z.object({ path: z.string() })) });
const agentMessage = (content: string) => ({ message: { customType: "dynaflows", content, display: false, attribution: "agent" as const } });
const tail = (text: string) => text.length > OUTPUT_TAIL ? `…${text.slice(-OUTPUT_TAIL)}` : text;

async function reviewTask(review: Review): Promise<string | undefined> {
	const [checks, diff, impact] = await Promise.all([review.checks, review.diff, review.impact]);
	if (!("failure" in impact) && impact.outcome === "no") return undefined;
	const judged = "failure" in impact ? `not judged: ${impact.failure}` : verdictText(impact);
	return `First task: the dynaflows extension changed (${review.from} → ${review.to}) since ${review.slug} last acknowledged it. ` +
		"Review the change and address anything that affects this repository's setup or its interactions with the rails first; " +
		"Git writes are refused until you call the dynaflows tool with acknowledge: true.\n" +
		`Extension impact: ${judged}\nExtension diff:\n${diff ?? "unavailable (not a Git revision pair)"}\nRecord checks:\n${checks}`;
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
		const found = sessions.get(id) ?? { book: new ObligationBook(), started: false };
		sessions.set(id, found);
		return found;
	};
	const structured = async (ctx: ExtensionContext, root: string) => await deps.modeOf(ctx, root) === "structured";
	const rolloutCommands = ["~/.omp", deps.configRoot].map(root => `python3 ${root}/scripts/device_rollout.py`);
	const judged = judgedRails(pi, { decide: deps.decide, structured, askFirst: deps.askFirst });

	async function reviewChecks(root: string): Promise<string> {
		const record = await deps.recordAt(root).catch(() => undefined);
		if (!record) return "no valid per-repo record; review the record against the change by hand.";
		const lines: string[] = [];
		for (const [label, prefix] of [["profile-check", deps.checks.profile], ["check", deps.checks.check]] as const) {
			const argv = [...prefix, "--repo", root, "--record", record.dir];
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
		const record = await deps.recordAt(root).catch(() => undefined);
		session.review = {
			slug, root, from, to, checks: reviewChecks(root), diff: extensionDiff(pi, deps.configRoot, deps.extensionDir, { from, to }),
			impact: judged.impactVerdict(ctx, { slug, record, configRoot: deps.configRoot, from, to }),
		};
	}

	pi.on("session_start", async (_event, ctx) => {
		if (deps.loadError() || ctx.agent.kind !== "main") return;
		const session = sessionFor(ctx);
		const root = await repoRoot(ctx.cwd);
		if (!root) return;
		const content = await readHandoff(root);
		if (content !== null) session.handoff = { root, content, injected: false };
		await startReview(ctx, root, session);
	});

	async function firstPromptMessages(session: Session): Promise<string[]> {
		const parts: string[] = [];
		if (session.review) {
			const task = await reviewTask(session.review);
			if (task) parts.push(task);
			else { await storeRevision(deps.reviewsPath, session.review.slug, session.review.to); session.review = undefined; }
		}
		if (session.handoff && !session.handoff.injected) {
			session.handoff.injected = true;
			parts.push(`Handoff from an earlier session (${session.handoff.root}/HANDOFF.md; deleted when this turn ends):\n${session.handoff.content}`);
		}
		return parts;
	}

	pi.on("before_agent_start", async (event, ctx) => {
		if (deps.loadError() || ctx.agent.kind !== "main" || event.reason !== "prompt") return;
		const session = sessionFor(ctx);
		const parts = session.started ? [] : await firstPromptMessages(session);
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

	async function trackPr(action: GitAction, ctx: ExtensionContext, session: Session): Promise<void> {
		if (!action.cwd) return;
		const root = await repoRoot(action.cwd);
		if (!root) return;
		const flag = action.args.findIndex(arg => arg === "--head" || arg === "-H");
		const branch = flag >= 0 ? action.args[flag + 1] : (await pi.exec("git", ["symbolic-ref", "--short", "-q", "HEAD"], { cwd: action.cwd, timeout: SUBPROCESS_TIMEOUT_MS })).stdout.trim();
		if (!branch) return;
		const cwd = action.cwd;
		session.book.add({ id: `pr:${branch}`, root, text: `the PR for ${branch} has green CI on its head and is unmerged; merge it (skill://git-tool/references/merge-and-recover.md)`,
			status: () => deps.prStatus(ctx, cwd, branch) });
		session.book.add(judged.evidenceObligation(ctx, { cwd, root, branch,
			requiredContext: async () => (await deps.recordAt(root).catch(() => undefined))?.gate.requiredCi?.requiredContext }));
	}

	async function afterMergeStep(root: string | undefined) {
		return root ? (await deps.recordAt(root).catch(() => undefined))?.gate.afterMerge : undefined;
	}

	// A merge needs the step unless the record names paths and the PR's files (when gh lists them) match none.
	async function mergeMatches(action: GitAction, cwd: string, paths: string[] | undefined): Promise<boolean> {
		if (!paths) return true;
		const selector = prSelector(action.args);
		const view = await pi.exec("gh", ["pr", "view", ...(selector ? [selector] : []), "--json", "files"], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (view.code !== 0) return true;
		return filesSchema.parse(JSON.parse(view.stdout)).files.some(item => matches(item.path, paths));
	}

	async function requireAfterMerge(action: GitAction, session: Session): Promise<void> {
		const root = action.cwd ? await repoRoot(action.cwd) : undefined;
		const step = await afterMergeStep(root);
		if (!root || !step || !action.cwd || !await mergeMatches(action, action.cwd, step.paths)) return;
		const command = shellJoin(step.argv);
		session.book.add({ id: `afterMerge:${root}`, root, text: `the record's afterMerge step has not exited 0 since the merge; run \`${command}\` in ${root}`,
			status: async () => "unmet" });
	}

	async function satisfied(event: ToolEvent, cwd: string, session: Session): Promise<void> {
		const command = event.input.command ?? "";
		if (ranCommand(command, rolloutCommands)) session.book.satisfy(ROLLOUT_ID);
		const root = await repoRoot(cwd);
		const step = await afterMergeStep(root);
		if (root && step && ranCommand(command, [shellJoin(step.argv)])) session.book.satisfy(`afterMerge:${root}`);
	}

	pi.on("tool_result", async (runtimeEvent, ctx) => {
		const event = policyEvent(runtimeEvent);
		if (deps.loadError() || event.toolName !== "bash" || event.isError || event.input.command === undefined) return;
		const session = sessionFor(ctx);
		const cwd = event.input.cwd ?? ctx.cwd;
		await satisfied(event, cwd, session);
		for (const action of await gitActions(event.input.command, cwd)) {
			if (/^gh pr create\b/.test(action.command)) await trackPr(action, ctx, session);
			if (/^gh pr merge\b/.test(action.command)) await requireAfterMerge(action, session);
		}
	});

	pi.on("session_stop", async (_event, ctx) => {
		if (deps.loadError()) return;
		const session = sessionFor(ctx);
		const extra = (await Promise.all(deps.sources.map(source => source(ctx.sessionManager.getSessionId())))).flat();
		const reason = await session.book.turnEnd(extra, root => structured(ctx, root), text => ctx.ui.notify(text, "error"));
		return reason ? { decision: "block" as const, reason } : undefined;
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
			const review = sessionFor(ctx).review;
			if (!review || !action.cwd || await repoRoot(action.cwd) !== review.root) return undefined;
			return `Agent rails: the dynaflows extension changed (${review.from} → ${review.to}) since ${review.slug} last acknowledged it; ` +
				"review the change, then call the dynaflows tool with acknowledge: true before Git writes.";
		},
		async commitReason(action) {
			return action.cwd ? stagedHandoffReason(pi, action.cwd) : undefined;
		},
		async optInRollout(ctx) {
			const root = await repoRoot(ctx.cwd);
			if (!root || !await structured(ctx, root)) return "Device rollout obligations need dynaflows structured mode; none was added.";
			sessionFor(ctx).book.add({ id: ROLLOUT_ID, root, text: `device rollout was requested; run \`${rolloutCommands[0]}\` until it exits 0`, status: async () => "unmet" });
			return `Device rollout requested: before this work ends, run \`${rolloutCommands[0]}\` until it exits 0.`;
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
export { ompJudge, registryDecide, type JudgeTransport } from "./judge.ts";
export { scopeInputSchema } from "./judged-rails.ts";
