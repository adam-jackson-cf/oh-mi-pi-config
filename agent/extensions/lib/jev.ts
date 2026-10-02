/**
 * Shared Jev (TypeSafe System One) client, policy modes and audit records for the
 * owner's Jev policy extensions. Not an extension itself: native discovery only
 * loads `*.ts` directly in `agent/extensions/` or a subdirectory `index.ts`.
 *
 * Evidence ladder the policies follow: deterministic rules first, a Jev decision
 * only for what rules cannot settle, an LLM only when Jev is unsure or the policy
 * escalates.
 */
import { randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { mkdir, open, readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { z } from "zod";

export const JEV_MODEL = "~typesafe/jev-latest";
// OpenRouter rejects versioned selectors; the resolved model is checked instead.
export const JEV_PINNED_MODEL = "typesafe/jev-1.13-20260917";
export const JEV_ENDPOINT = "https://openrouter.ai/api/alpha/decisions";
// Jev 1.13 accepts ~32k tokens of state plus questions; keep state well under it.
export const JEV_STATE_CHAR_LIMIT = 96_000;
const DEFAULT_TIMEOUT_MS = 15_000;

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

/** Structured forms per docs.typesafe.ai: a question with optional focus, criteria with what / not_for / examples. */
export type JevInstructions = string | { question: string; focus?: string };
export type JevCriterion = string | { what: string; not_for?: string; examples?: string[] };
export type NoulQuestion = { type: "noul"; instructions: JevInstructions; criteria?: { true?: JevCriterion; false?: JevCriterion } };
export type ChoiceQuestion = { type: "choice"; instructions: string; criteria: Record<string, string | null> };
export type ScoreQuestion = { type: "score"; instructions: string; criteria: string[] };
export type JevQuestion = NoulQuestion | ChoiceQuestion | ScoreQuestion;
export type JevQuestions = Record<string, JevQuestion>;

const probability = z.number().min(0).max(1);
const noulAnswer = z.object({ type: z.literal("noul"), noul: probability });
const choiceAnswer = z.object({
  type: z.literal("choice"), choice: z.string(), probabilities: z.record(z.string(), probability), confidence: probability,
});
const scoreAnswer = z.object({
  type: z.literal("score"), score: z.number().nonnegative(), probabilities: z.record(z.string(), probability),
  confidence: probability, legend: z.record(z.string(), z.string()).optional(),
});
const answer = z.discriminatedUnion("type", [noulAnswer, choiceAnswer, scoreAnswer]);
export type JevAnswer = z.infer<typeof answer>;
export type JevAnswers = Record<string, JevAnswer>;
const decisionResponse = z.object({
  id: z.string(), model: z.string(), provider: z.string().optional(),
  answers: z.record(z.string(), answer),
  usage: z.object({ input_tokens: z.number().nonnegative(), output_tokens: z.number().nonnegative(), cost: z.number().nonnegative() }),
});

export type JevSuccess = {
  ok: true; answers: JevAnswers; resolvedModel: string; providerResponseId: string;
  costUsd: number; inputTokens: number; latencyMs: number;
};
export type JevFailure = { ok: false; error: string; httpStatus?: number; resolvedModel?: string; latencyMs: number };
export type JevResult = JevSuccess | JevFailure;
export type JevRequestOptions = { signal?: AbortSignal; timeoutMs?: number };

const QUESTION_ID = /^[a-z][a-z0-9_]{0,63}$/;
const instructionsSchema = z.union([z.string().trim().min(1), z.object({ question: z.string().trim().min(1), focus: z.string().optional() })]);

/** Rejects malformed question blocks before any network call; these are programmer errors. */
export function validateQuestions(questions: JevQuestions): void {
  const entries = Object.entries(questions);
  if (entries.length === 0) throw new Error("Jev needs at least one question.");
  for (const [id, question] of entries) {
    if (!QUESTION_ID.test(id)) throw new Error(`Jev question id "${id}" must match ${QUESTION_ID}.`);
    if (!instructionsSchema.safeParse(question.instructions).success) throw new Error(`Jev question "${id}" has empty instructions.`);
    if (question.type === "choice") {
      const options = Object.keys(question.criteria);
      if (options.length < 2 || options.length > 255) throw new Error(`Jev choice "${id}" needs 2-255 options.`);
    }
    if (question.type === "score" && (question.criteria.length < 2 || question.criteria.length > 10)) {
      throw new Error(`Jev score "${id}" needs 2-10 levels.`);
    }
  }
}

function answerMatches(question: JevQuestion, given: JevAnswer | undefined): boolean {
  if (!given || given.type !== question.type) return false;
  if (question.type === "choice" && given.type === "choice") {
    return Object.hasOwn(question.criteria, given.choice) &&
      Object.keys(question.criteria).every((option) => given.probabilities[option] !== undefined);
  }
  if (question.type === "score" && given.type === "score") return given.score <= question.criteria.length - 1;
  return true;
}

/**
 * One Jev decision. Never throws for transport, provider or schema failures; returns
 * `ok: false` with a safe message and no provider body (bodies can echo input).
 */
export async function decide(apiKey: string, state: JsonValue, questions: JevQuestions,
  options?: JevRequestOptions): Promise<JevResult> {
  validateQuestions(questions);
  const started = Date.now();
  const timeout = AbortSignal.timeout(options?.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  const signal = options?.signal ? AbortSignal.any([options.signal, timeout]) : timeout;
  let response: Response;
  try {
    response = await fetch(JEV_ENDPOINT, {
      method: "POST", signal,
      headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
      body: JSON.stringify({ model: JEV_MODEL, state, questions }),
    });
  } catch (cause) {
    const aborted = cause instanceof Error && (cause.name === "AbortError" || cause.name === "TimeoutError");
    return { ok: false, error: aborted ? "Jev request timed out or was cancelled." : "Jev request failed to reach OpenRouter.",
      latencyMs: Date.now() - started };
  }
  if (!response.ok) {
    const error = response.status === 402
      ? "OpenRouter is out of credits or at its spending limit (HTTP 402)."
      : `Jev Decisions API returned HTTP ${response.status}.`;
    return { ok: false, error, httpStatus: response.status, latencyMs: Date.now() - started };
  }
  let parsed: z.infer<typeof decisionResponse>;
  try {
    parsed = decisionResponse.parse(await response.json());
  } catch {
    return { ok: false, error: "Jev returned an invalid decision response.", latencyMs: Date.now() - started };
  }
  const latencyMs = Date.now() - started;
  const resolvedModel = /^[\w./~-]{1,120}$/.test(parsed.model) ? parsed.model : "invalid-model-identifier";
  if (resolvedModel !== JEV_PINNED_MODEL) {
    return { ok: false, error: "Jev resolved to an unexpected model version; re-evaluate before trusting it.",
      resolvedModel, latencyMs };
  }
  for (const [id, question] of Object.entries(questions)) {
    if (!answerMatches(question, parsed.answers[id])) {
      return { ok: false, error: `Jev answer for "${id}" is missing or does not match the question.`, resolvedModel, latencyMs };
    }
  }
  return { ok: true, answers: parsed.answers, resolvedModel, providerResponseId: parsed.id,
    costUsd: parsed.usage.cost, inputTokens: parsed.usage.input_tokens, latencyMs };
}

export function redact(text: string, apiKey?: string): string {
  let out = apiKey ? text.replaceAll(apiKey, "[REDACTED]") : text;
  out = out.replace(/\b(?:sk-[\w-]{8,}|gh[pousr]_[\w-]{8,}|github_pat_[\w-]{8,}|AKIA[0-9A-Z]{16})\b/g, "[REDACTED]");
  out = out.replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, "[REDACTED PRIVATE KEY]");
  out = out.replace(/\b(Bearer\s+)[\w~+/-]+(?:\.[\w~+/-]+)*/gi, "$1[REDACTED]");
  out = out.replace(/\beyJ[\w-]+\.[\w-]+\.[\w-]+\b/g, "[REDACTED]");
  return out.replace(/\b((?:[\w.-]*(?:api[_-]?key|token|secret|password|passwd|authorization))\s*(?:[:=]|\bis\b)\s*['"]?)[^\s'",;]+/gi, "$1[REDACTED]");
}

export function redactValue(value: JsonValue, apiKey?: string): JsonValue {
  if (value === null || value === true || value === false) return value;
  if (Array.isArray(value)) return value.map((item) => redactValue(item, apiKey));
  if (value instanceof Object) {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, redactValue(item, apiKey)]));
  }
  // Remaining values are strings or numbers; Number.isFinite does not coerce strings.
  return Number.isFinite(value) ? value : redact(String(value), apiKey);
}

export type Clipped = { text: string; omitted: number };

/** Keep the head (and a short tail) of long text with a visible marker. */
export function clip(text: string, limit: number): Clipped {
  if (text.length <= limit) return { text, omitted: 0 };
  const tail = Math.min(Math.floor(limit / 5), 2_000);
  const head = limit - tail;
  const omitted = text.length - head - tail;
  return { text: `${text.slice(0, head)}\n…[${omitted} characters omitted]…\n${text.slice(text.length - tail)}`, omitted };
}

export type PolicyMode = "off" | "shadow" | "enforce";
const policyModes = z.record(z.string(), z.enum(["off", "shadow", "enforce"]));

export function policiesFile(): string {
  return process.env.JEV_POLICIES_FILE ?? join(homedir(), ".omp", "agent", "jev-policies.json");
}

/**
 * Per-policy modes from `agent/jev-policies.json` (keys starting with `$` are
 * comments). A missing file means every policy uses its fallback; a malformed
 * file throws so the misconfiguration is visible at load.
 */
export async function readPolicyModes(file = policiesFile()): Promise<Record<string, PolicyMode>> {
  let raw: string;
  try {
    raw = await readFile(file, "utf8");
  } catch {
    return {};
  }
  const decoded = z.record(z.string(), z.string()).parse(JSON.parse(raw));
  return policyModes.parse(Object.fromEntries(Object.entries(decoded).filter(([key]) => !key.startsWith("$"))));
}

export function policyMode(modes: Record<string, PolicyMode>, policy: string, fallback: PolicyMode = "shadow"): PolicyMode {
  return modes[policy] ?? fallback;
}

export type AgentKind = "main" | "sub";
type DecisionStage = "deterministic" | "jev" | "jev_error" | "skipped";

/** One policy decision; the workbench (`~/.omp/jev-lab`) reads and labels these. */
export type DecisionRecord = {
  schema: 1; type: "decision"; timestamp: string; requestId: string;
  policy: string; policyVersion: string; mode: PolicyMode;
  sessionId?: string; agentKind?: AgentKind; agentName?: string;
  stage: DecisionStage;
  /** Deterministic rule id that settled the decision, when stage is `deterministic`. */
  rule?: string;
  /** Short redacted description: a command, path, agent name. */
  subject: string;
  state?: JsonValue; questions?: JevQuestions; answers?: JevAnswers;
  resolvedModel?: string; providerResponseId?: string; costUsd?: number; latencyMs?: number;
  error?: string; httpStatus?: number;
  /** Policy-specific outcome, e.g. allow | flag | confirm | block | light | standard | deep. */
  verdict: string;
  /** True only when the verdict changed agent behaviour (enforce mode). */
  enforced: boolean;
  /** Label vocabulary offered to the human reviewer for this record. */
  labels: string[];
};
export type LabelReviewer = "human" | "agent";
export type LabelRecord = {
  schema: 1; type: "label"; timestamp: string; requestId: string; policy: string;
  label: string; reviewer: LabelReviewer; note?: string;
};
export type NewDecision = Omit<DecisionRecord, "schema" | "type" | "timestamp" | "requestId">;

export function auditRoot(): string {
  return process.env.JEV_AUDIT_DIR ?? join(homedir(), ".omp", "agent", "jev-audit");
}

async function appendLine(file: string, line: string): Promise<void> {
  await mkdir(dirname(file), { recursive: true, mode: 0o700 });
  const handle = await open(file, constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND | constants.O_NOFOLLOW, 0o600);
  try {
    await handle.chmod(0o600);
    await handle.writeFile(line + "\n");
  } finally {
    await handle.close();
  }
}

/**
 * Append a decision to `<auditRoot>/<policy>/<YYYY-MM-DD>.jsonl`. Returns the record,
 * or undefined when `JEV_AUDIT=0`. Callers redact `subject`/`state` before calling.
 */
export async function appendDecision(decision: NewDecision): Promise<DecisionRecord | undefined> {
  if (process.env.JEV_AUDIT === "0") return undefined;
  const timestamp = new Date().toISOString();
  const record: DecisionRecord = { schema: 1, type: "decision", timestamp, requestId: `jevp_${randomUUID()}`, ...decision };
  await appendLine(join(auditRoot(), decision.policy, `${timestamp.slice(0, 10)}.jsonl`), JSON.stringify(record));
  return record;
}

export async function appendLabel(label: Omit<LabelRecord, "schema" | "type" | "timestamp">): Promise<LabelRecord> {
  const record: LabelRecord = { schema: 1, type: "label", timestamp: new Date().toISOString(), ...label };
  await appendLine(join(auditRoot(), label.policy, "labels.jsonl"), JSON.stringify(record));
  return record;
}
