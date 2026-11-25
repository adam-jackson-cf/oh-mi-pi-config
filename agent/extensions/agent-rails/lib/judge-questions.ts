// Question parsing and answer shaping for judged rails. The compiled omp binary serves extensions
// only its public package exports; the eval kernel's own parsers sit in a private module
// (eval/judgment-bridge), so rails keep these two small steps here.
import type { Answer, JudgmentResult, NoulQuestion, Question, Questions } from "@oh-my-pi/pi-ai";
import { z } from "@oh-my-pi/pi-coding-agent";

const instructions = z.string().min(1);
const boolQuestion = z.object({
	type: z.literal("bool"),
	instructions,
	criteria: z.object({ true: z.string().optional(), false: z.string().optional() }).optional(),
});
const choiceQuestion = z.object({ type: z.literal("choice"), instructions, criteria: z.record(z.string(), z.string().nullable()) });
const scoreQuestion = z.object({ type: z.literal("score"), instructions, criteria: z.array(z.string()).min(2) });
/** Questions as the judgment registry writes them, keyed by id. */
export const registryQuestions = z.record(z.string(), z.union([boolQuestion, choiceQuestion, scoreQuestion]));
export type RegistryQuestions = z.infer<typeof registryQuestions>;
type RegistryQuestion = RegistryQuestions[string];

function toNoul(question: z.infer<typeof boolQuestion>): NoulQuestion {
	const noul: NoulQuestion = { type: "noul", instructions: question.instructions };
	const { criteria } = question;
	if (!criteria) return noul;
	const sides: NonNullable<NoulQuestion["criteria"]> = {};
	if (criteria.true !== undefined) sides.true = criteria.true;
	if (criteria.false !== undefined) sides.false = criteria.false;
	noul.criteria = sides;
	return noul;
}

function toQuestion(id: string, question: RegistryQuestion): Question {
	switch (question.type) {
		case "bool":
			return toNoul(question);
		case "choice":
			if (Object.keys(question.criteria).length < 2) throw new Error(`choice question "${id}" needs at least two options`);
			return { type: "choice", instructions: question.instructions, criteria: question.criteria };
		case "score": {
			const [lowest, second, ...rest] = question.criteria;
			if (lowest === undefined || second === undefined) throw new Error(`score question "${id}" needs at least two levels`);
			return { type: "score", instructions: question.instructions, criteria: [lowest, second, ...rest] };
		}
	}
}

/** Registry questions as omp's judge takes them; `bool` maps to the library's `noul`. */
export function toJudgeQuestions(questions: RegistryQuestions): Record<string, Question> {
	const ids = Object.keys(questions);
	if (ids.length === 0) throw new Error("questions must contain at least one question");
	return Object.fromEntries(ids.map(id => [id, toQuestion(id, questions[id]!)]));
}

/** An answer as the registry reads it: library `noul` answers surface as `bool`. */
export type RegistryAnswer = Answer | { type: "bool"; bool: number };
export type JudgeResult = { answers: Record<string, RegistryAnswer>; model: string };

/** The judge's answers keyed by question id, plus the provider/model that produced them. */
export function toJudgeResult(result: JudgmentResult<Questions>): JudgeResult {
	const answers: Record<string, RegistryAnswer> = {};
	for (const [id, answer] of Object.entries(result.answers)) {
		answers[id] = answer.type === "noul" ? { type: "bool", bool: answer.noul } : answer;
	}
	return { answers, model: `${result.provider}/${result.model}` };
}
