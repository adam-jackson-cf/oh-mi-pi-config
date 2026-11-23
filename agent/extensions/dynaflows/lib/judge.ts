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
export type JudgeQuestions = z.infer<typeof questionsSchema>["questions"];
export type JudgeAnswer = { answers: JudgeResult["answers"]; model: string };
/** One judge call: the answers and the provider/model that produced them. Tests stub this. */
export type JudgeTransport = (state: JudgeState, questions: JudgeQuestions) => Promise<JudgeAnswer>;
export type Verdict = { judgment: RailJudgment; outcome: "yes" | "no" | "escalate"; probability: number; call: JudgmentCall };
export type Decide = (name: RailJudgment, state: JudgeState, ctx: ExtensionContext) => Promise<Verdict>;

const refusalSchema = z.object({ error: z.string() });
export const jsonText = z.string().transform(text => { try { return z.unknown().parse(JSON.parse(text)); } catch { return undefined; } });

/** decide() for rails: the registry validates and logs; the host transport answers in between. */
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
	return async (name, state, ctx) => {
		const asked = questionsSchema.parse(jsonText.parse(await run(["questions", name], ctx.cwd)));
		const answer = await transportFor(ctx)(state, asked.questions);
		const dir = await mkdtemp(join(tmpdir(), "dynaflows-decide-"));
		try {
			const request = join(dir, "answers.json");
			await writeFile(request, JSON.stringify({ state, answers: answer.answers, model: answer.model }));
			const call = judgmentCallSchema.parse(jsonText.parse(await run(["record", name, request], ctx.cwd)));
			const question = RAIL_JUDGMENTS[name];
			const outcome = call.outcomes[question];
			const probability = z.number().safeParse(call.probabilities[question]);
			if (!outcome || !probability.success) throw new Error(`${name}: the registry returned no ${question} outcome`);
			return { judgment: name, outcome, probability: probability.data, call };
		} finally { await rm(dir, { recursive: true, force: true }); }
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
export const verdictText = (verdict: Verdict): string =>
	`${verdict.judgment}/${RAIL_JUDGMENTS[verdict.judgment]} ${verdict.outcome}, P=${verdict.probability.toFixed(2)}`;

// An owner approval is a line of its own in the owner's prompt; agents cannot write owner prompts.
const APPROVE = /^\$approve ((?:in-scope|implementation-integrity|completion-evidence):[0-9a-f]{12,64})$/;
export const approvalLine = (key: string): string => `\`$approve ${key}\``;

/** Escalated decisions the owner approved, per session, keyed <judgment>:<subject digest or SHA>. */
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
}
