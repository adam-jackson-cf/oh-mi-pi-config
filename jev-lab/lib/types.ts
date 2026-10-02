import { z } from "zod";
import type { JsonValue, JevQuestions, LabelReviewer } from "../../agent/extensions/lib/jev";

export const jsonValue = z.json();

const probability = z.number().min(0).max(1);
export const questionSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("noul"), instructions: z.string(),
    criteria: z.object({ true: z.string().optional(), false: z.string().optional() }).optional() }),
  z.object({ type: z.literal("choice"), instructions: z.string(), criteria: z.record(z.string(), z.string().nullable()) }),
  z.object({ type: z.literal("score"), instructions: z.string(), criteria: z.array(z.string()) }),
]);
export const questionsSchema = z.record(z.string(), questionSchema);

/** Answer as stored or freshly returned; fields depend on `type`. */
export const answerSchema = z.object({
  type: z.enum(["noul", "choice", "score"]),
  noul: probability.optional(),
  choice: z.string().optional(),
  score: z.number().optional(),
  probabilities: z.record(z.string(), z.number()).optional(),
  confidence: z.number().optional(),
});
export type LabAnswer = z.infer<typeof answerSchema> & { id: string };

export type SourceKind = "jev-scope" | "policy" | "caseset";
export type Source = { id: string; kind: SourceKind; title: string; count: number };

export type Proposal = {
  label: string; agreement: "agreed" | "disputed"; rationale: string; namedChange?: string; evidence?: string;
  labellers: Record<string, string>;
};

/** One reviewable Jev decision, normalised across all source kinds. */
export type LabCase = {
  source: string;
  id: string;
  timestamp?: string;
  subject: string;
  stage?: string;
  verdict: string;
  /** P(yes) or equivalent binary score used for threshold sweeps. */
  score?: number;
  /** 0 (certain) .. 1 (coin flip). */
  uncertainty: number;
  /** Rubric input sufficiency; only defined for jev-scope. */
  sufficient?: boolean;
  taskSource?: string;
  version?: string;
  /** jev-scope only: whether the audited session was a main session or a subagent, and the subagent's id. */
  sessionKind?: "main" | "sub";
  agentId?: string;
  state: JsonValue;
  questions?: JevQuestions;
  answers: LabAnswer[];
  resolvedModel?: string;
  costUsd?: number;
  latencyMs?: number;
  transcriptPath?: string;
  error?: string;
  labelOptions: string[];
  positiveLabel?: string;
  label: string | null;
  /** Who wrote the effective label: a human always wins over an agent first-pass label. */
  labelBy: LabelReviewer | null;
  labelNote?: string;
  /** How the effective label was produced (e.g. agent_first_pass_agreement); absent for legacy labels. */
  labelBasis?: string;
  proposal?: Proposal;
  expected?: Record<string, string | number | boolean>;
  note?: string;
  /** Policy decisions: the deterministic rule id, when a rule decided. */
  rule?: string;
};

export type LabPaths = { sessionsDir: string; auditDir: string; casesetDir: string; runsDir: string };

export class LabError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "LabError";
  }
}

/** Uncertainty of a binary probability. */
export function binaryUncertainty(p: number): number {
  return 1 - Math.abs(2 * p - 1);
}

/** Uncertainty from the strongest option of a choice-like probability map. */
export function choiceUncertainty(probabilities: Record<string, number> | undefined): number {
  const values = Object.values(probabilities ?? {});
  return values.length ? 1 - Math.max(...values) : 1;
}
