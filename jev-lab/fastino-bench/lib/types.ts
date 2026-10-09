// Shared contract for the Fastino benchmark: one Jev-format case is replayed against every backend.
import { z } from "zod";
import type { JevQuestions, JsonValue } from "../../../agent/extensions/lib/jev";

export type { JevQuestions, JsonValue };

/** Ground truth per question: noul → boolean, choice → option key, score → level index. */
export type Truth = Record<string, boolean | string | number>;

/** One line of `datasets/<experiment>/<dataset>.jsonl`. */
export type BenchCase = {
  id: string;
  experiment: string;
  dataset: string;
  /** Exactly what Jev is (or would be) sent. */
  state: JsonValue;
  questions: JevQuestions;
  /** Questions without a truth entry are run but not scored. */
  truth: Truth;
  /** The question the integration acts on; headline metrics use it. */
  primary: string;
  /** For a choice primary: the option treated as the positive class in binary metrics. */
  positive?: string;
  /**
   * Integration-level truth when per-question truth is unknown (e.g. a guard should block/flag).
   * `risk` lists the signals: "q" = P(true) of noul q, "q:opt" = P(opt) of choice q; combined by
   * max() (default: any signal acts) or product (all conditions must hold).
   */
  act?: { truth: boolean; risk: string[]; mode?: "max" | "product" };
  /** Free-form stratification fields, e.g. { bucket: "8k", depth: 0.75, kind: "temporal" }. */
  meta?: Record<string, JsonValue>;
};

export type BackendId = "jev" | "glide" | "decide" | "decide-keys";

/** Normalised answer: probabilities over the question's options ("true"/"false" for noul, "0".."N-1" for score). */
export type NormAnswer = {
  top: string;
  probs: Record<string, number>;
  /** noul only: P(true). */
  pTrue?: number;
  /** score only: Σ level × p. */
  expected?: number;
};

/** One line of `runs/<experiment>/<backend>.jsonl`. */
export type RunResult = {
  id: string;
  backend: BackendId;
  ok: boolean;
  error?: string;
  httpStatus?: number;
  latencyMs: number;
  inputTokens?: number;
  costUsd?: number;
  /** Response model identifier as returned by the provider. */
  model?: string;
  /** Characters of serialised state dropped to fit the backend's context; 0 when nothing was dropped. */
  clippedChars?: number;
  answers?: Record<string, NormAnswer>;
  timestamp: string;
  /** Repeat index for stability runs; 0 otherwise. */
  rep: number;
};

// Runtime schemas for the JSONL boundaries; `satisfies` keeps them locked to the types above.
export const jsonValueSchema: z.ZodType<JsonValue> = z.lazy(() => z.union([
  z.string(), z.number(), z.boolean(), z.null(), z.array(jsonValueSchema), z.record(z.string(), jsonValueSchema),
]));

const criterionSchema = z.union([
  z.string(),
  z.object({ what: z.string(), not_for: z.string().optional(), examples: z.array(z.string()).optional() }),
]);
const questionSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("noul"),
    instructions: z.union([z.string(), z.object({ question: z.string(), focus: z.string().optional() })]),
    criteria: z.object({ true: criterionSchema.optional(), false: criterionSchema.optional() }).optional(),
  }),
  z.object({ type: z.literal("choice"), instructions: z.string(), criteria: z.record(z.string(), z.string().nullable()) }),
  z.object({ type: z.literal("score"), instructions: z.string(), criteria: z.array(z.string()) }),
]);

export const benchCaseSchema = z.object({
  id: z.string(),
  experiment: z.string(),
  dataset: z.string(),
  state: jsonValueSchema,
  questions: z.record(z.string(), questionSchema),
  truth: z.record(z.string(), z.union([z.boolean(), z.string(), z.number()])),
  primary: z.string(),
  positive: z.string().optional(),
  act: z.object({ truth: z.boolean(), risk: z.array(z.string()), mode: z.enum(["max", "product"]).optional() }).optional(),
  meta: z.record(z.string(), jsonValueSchema).optional(),
}) satisfies z.ZodType<BenchCase>;

const normAnswerSchema = z.object({
  top: z.string(),
  probs: z.record(z.string(), z.number()),
  pTrue: z.number().optional(),
  expected: z.number().optional(),
}) satisfies z.ZodType<NormAnswer>;

export const runResultSchema = z.object({
  id: z.string(),
  backend: z.enum(["jev", "glide", "decide", "decide-keys"]),
  ok: z.boolean(),
  error: z.string().optional(),
  httpStatus: z.number().optional(),
  latencyMs: z.number(),
  inputTokens: z.number().optional(),
  costUsd: z.number().optional(),
  model: z.string().optional(),
  clippedChars: z.number().optional(),
  answers: z.record(z.string(), normAnswerSchema).optional(),
  timestamp: z.string(),
  rep: z.number(),
}) satisfies z.ZodType<RunResult>;
