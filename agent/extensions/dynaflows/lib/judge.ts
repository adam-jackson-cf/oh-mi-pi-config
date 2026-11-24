// Judged rails: one decide() call through the judgment registry (engine/workflows.py, run by
// lib/decide.py) with the judge transport the omp host supplies, plus owner approvals for escalations.
import { createHash } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { z, type ExtensionAPI, type ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import type { Settings } from "@oh-my-pi/pi-coding-agent/config/settings";
import { resolveJudge, sharedJudgmentCache, type JudgeDeps } from "@oh-my-pi/pi-coding-agent/judgment";
import { registryQuestions, toJudgeQuestions, toJudgeResult, type JudgeResult } from "./judge-questions.ts";
import { judgmentCallSchema, type JudgmentCall } from "./judgment-calls.ts";
import { SUBPROCESS_TIMEOUT_MS } from "./rails-runtime.ts";

type ExecApi = Pick<ExtensionAPI, "exec">;
/** Each rail judgment and the one bool question its rail reads. */
export const RAIL_JUDGMENTS = {
	"extension-impact": "impact",
	"in-scope": "ask_first",
	"implementation-integrity": "weakens_verification",
	"completion-evidence": "evidence_gap",
} as const;
export type RailJudgment = keyof typeof RAIL_JUDGMENTS;
/** A judged state: the JSON object omp's judge() takes. */
export type JudgeState = Exclude<Parameters<ReturnType<typeof resolveJudge>["judge"]>[0]["state"], string | readonly unknown[]>;
const questionsSchema = z.object({ judgment: z.string(), model: z.string().min(1), questions: registryQuestions }).strict();
// The registry's answer when the repo config turns every question of the judgment off.
const offSchema = z.object({ judgment: z.string(), off: z.literal(true) }).strict();
export type JudgeQuestions = z.infer<typeof questionsSchema>["questions"];
export type JudgeAnswer = { answers: JudgeResult["answers"]; model: string };
/** One judge call: the answers and the provider/model that produced them. Tests stub this. */
export type JudgeTransport = (state: JudgeState, questions: JudgeQuestions) => Promise<JudgeAnswer>;
/** A judged verdict: a shadow one is logged and shown but never acts, so the rail behaves as if the judgment were absent. */
export type JudgedVerdict = { judgment: RailJudgment; outcome: "yes" | "no" | "escalate"; probability: number; mode: "enforced" | "shadow"; call: JudgmentCall };
/** The repo config turns the rail's question off: no judge call, no decision store row, and the rail never acts on it. */
export type OffVerdict = { judgment: RailJudgment; mode: "off" };
export type Verdict = JudgedVerdict | OffVerdict;
/** What a rail judged, for the decision store row: the event that asked and the subject. */
export type DecisionSubject = {
	trigger: "commit" | "prove" | "pr" | "manual"; repo?: string; commit?: string; pr?: number; item_kind?: string; item?: string;
};
export type Decide = (name: RailJudgment, state: JudgeState, ctx: ExtensionContext, subject: DecisionSubject) => Promise<Verdict>;
/** What the registry bridge's record and error commands read: the judged state, its subject, and the answers or the failure. */
type BridgeRequest = { state: JudgeState; context: DecisionSubject } & ({ answers: JudgeAnswer["answers"]; model: string } | { error: string });

const refusalSchema = z.object({ error: z.string() });
export const jsonText = z.string().transform(text => { try { return z.unknown().parse(JSON.parse(text)); } catch { return undefined; } });

/** decide() for rails: the registry validates and logs to the decision store; the host transport answers in between.
 * A transport failure is logged as an error decision before it is rethrown. */
export function registryDecide(pi: ExecApi, bridge: string[], transportFor: (ctx: ExtensionContext) => JudgeTransport): Decide {
	const [first, ...prefix] = bridge;
	if (!first) throw new Error("the judgment registry command is empty");
	const command: string = first;
	async function run(args: string[], cwd: string): Promise<string> {
		const result = await pi.exec(command, [...prefix, ...args], { cwd, timeout: SUBPROCESS_TIMEOUT_MS });
		if (result.code === 0 && !result.killed) return result.stdout;
		const refusal = refusalSchema.safeParse(jsonText.parse(result.stdout));
		throw new Error(`judgment registry ${args[0]} ${args[1]} failed${result.killed ? " (killed)" : ""}: ${
			refusal.success ? refusal.data.error : `${result.stdout}\n${result.stderr}`.trim()}`);
	}
	async function bridged(verb: "record" | "error", name: RailJudgment, request: BridgeRequest, cwd: string): Promise<string> {
		const dir = await mkdtemp(join(tmpdir(), "dynaflows-decide-"));
		try {
			const path = join(dir, "request.json");
			await writeFile(path, JSON.stringify(request));
			return await run([verb, name, path], cwd);
		} finally { await rm(dir, { recursive: true, force: true }); }
	}
	async function answered(name: RailJudgment, state: JudgeState, ctx: ExtensionContext, subject: DecisionSubject, questions: JudgeQuestions): Promise<JudgeAnswer> {
		try { return await transportFor(ctx)(state, questions); }
		catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			const logged = await bridged("error", name, { state, error: message, context: subject }, ctx.cwd).then(() => "", (logError: Error) => `; the decision store could not log it: ${logError.message}`);
			throw new Error(`${message}${logged}`, { cause: error });
		}
	}
	return async (name, state, ctx, subject) => {
		const asked = z.union([questionsSchema, offSchema]).parse(jsonText.parse(await run(["questions", name], ctx.cwd)));
		if ("off" in asked) return { judgment: name, mode: "off" };
		const answer = await answered(name, state, ctx, subject, asked.questions);
		const call = judgmentCallSchema.parse(jsonText.parse(await bridged("record", name, { state, answers: answer.answers, model: answer.model, context: subject }, ctx.cwd)));
		const question = RAIL_JUDGMENTS[name];
		const outcome = call.outcomes[question];
		const mode = call.modes[question];
		const probability = z.number().safeParse(call.probabilities[question]);
		if (!outcome || !mode || !probability.success) throw new Error(`${name}: the registry returned no ${question} outcome`);
		return { judgment: name, outcome, probability: probability.data, mode, call };
	};
}

/** The omp judge role, as the eval kernel's judge() resolves it, with the answering model reported. */
export function ompJudge(settings: Settings): (ctx: ExtensionContext) => JudgeTransport {
	return ctx => async (state, questions) => {
		const deps: JudgeDeps = { settings, registry: ctx.modelRegistry, sessionId: ctx.sessionManager.getSessionId(), purpose: "dynaflows-rails" };
		const cache = sharedJudgmentCache();
		if (cache) deps.cache = cache;
		const result = toJudgeResult(await resolveJudge(deps).judge({ state, questions: toJudgeQuestions(questions) }));
		return { answers: result.answers, model: result.model };
	};
}

export const digest = (value: JudgeState): string => createHash("sha256").update(JSON.stringify(value)).digest("hex");
export const clip = (text: string, limit: number): string => text.length > limit ? `${text.slice(0, limit)}…[truncated]` : text;
export const verdictText = (verdict: Verdict): string => verdict.mode === "off"
	? `${verdict.judgment}/${RAIL_JUDGMENTS[verdict.judgment]} is off in this repository's config (not judged)`
	: `${verdict.judgment}/${RAIL_JUDGMENTS[verdict.judgment]} ${verdict.outcome}, P=${verdict.probability.toFixed(2)}${verdict.mode === "shadow" ? " (shadow, not blocking)" : ""}`;
/** Whether a verdict acts on the rail: enforced verdicts do; shadow verdicts are logged only; off verdicts were never judged. */
export const enforced = (verdict: Verdict): verdict is JudgedVerdict & { mode: "enforced" } => verdict.mode === "enforced";

// An owner approval is a line of its own in the owner's prompt; agents cannot write owner prompts.
// Escalated judgments are keyed by a digest or SHA; autonomy asks by action and subject (branch or PR number).
const APPROVE = /^\$approve ((?:in-scope|implementation-integrity|completion-evidence):[0-9a-f]{12,64}|autonomy:(?:branch|pr|merge):[A-Za-z0-9._/#-]+)$/;
export const approvalLine = (key: string): string => `\`$approve ${key}\``;

/**
 * Owner approvals, per session and in memory: escalated decisions keyed <judgment>:<subject digest or SHA>,
 * and autonomy asks keyed autonomy:<action>:<subject>, which the rails consume on the first allowed action.
 */
export class Approvals {
	#granted = new Map<string, Set<string>>();

	fromPrompt(sessionId: string, prompt: string): string[] {
		const keys = prompt.split("\n").flatMap(line => APPROVE.exec(line.trim())?.[1] ?? []);
		const granted = this.#granted.get(sessionId) ?? new Set<string>();
		for (const key of keys) granted.add(key);
		this.#granted.set(sessionId, granted);
		return keys;
	}

	has(sessionId: string, key: string): boolean {
		return this.#granted.get(sessionId)?.has(key) ?? false;
	}

	/** Uses a one-use approval up: a later action needs the owner's approval again. */
	consume(sessionId: string, key: string): void {
		this.#granted.get(sessionId)?.delete(key);
	}
}
