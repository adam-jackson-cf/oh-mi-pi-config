// Backend adapters: one Jev-format case in, normalised answers out. Never throws for provider
// failures; never returns or logs provider bodies (they can echo input) or keys.
import { z } from "zod";
import { decide as jevDecide } from "../../../agent/extensions/lib/jev";
import type { JevCriterion, JevInstructions, JevQuestion, JevQuestions, JsonValue } from "../../../agent/extensions/lib/jev";
import type { NormAnswer, RunResult } from "./types";

export type Backend = {
  id: RunResult["backend"];
  call(state: JsonValue, questions: JevQuestions): Promise<Omit<RunResult, "id" | "backend" | "timestamp" | "rep">>;
};

const FASTINO = "https://api.fastino.ai";
const GLIDE_MODEL = "fastino/GLiDE";
const DECIDE_MODEL = "fastino/GLiNER-2.5-Decide";
// Published list prices (USD per 1M input tokens), GET /v1/base-models on 2026-10-02.
const GLIDE_PRICE = 0.30;
const DECIDE_PRICE = 0.03;
// Decide's window is 8,192 tokens; schema text shares it. ~3.5 chars/token for JSON-heavy state.
const DECIDE_STATE_CHARS = 24_000;
// Documented retryable statuses plus gateway failures (502/504) observed from the hosted endpoint.
const RETRYABLE = new Set([425, 429, 502, 503, 504]);

const describable = z.union([
  z.string(),
  z.object({ question: z.string(), focus: z.string().optional() })
    .transform(o => (o.focus ? `${o.question} Focus: ${o.focus}` : o.question)),
  z.object({ what: z.string(), not_for: z.string().optional(), examples: z.array(z.string()).optional() })
    .transform(o => {
      const parts = [o.what];
      if (o.not_for !== undefined) parts.push(`Not for: ${o.not_for}`);
      if (o.examples?.length) parts.push(`Examples: ${o.examples.join("; ")}`);
      return parts.join(" ");
    }),
]);

function textOf(value: JevInstructions | JevCriterion): string {
  return describable.parse(value);
}

type GlideBase = { type: string; instructions: string };
type GlideQuestion = GlideBase | (GlideBase & { criteria: Record<string, string> | string[] });

/** Jev structured instructions/criteria → the plain strings GLiDE requires. */
export function toGlideQuestions(questions: JevQuestions) {
  const out: Record<string, GlideQuestion> = {};
  for (const [id, q] of Object.entries(questions)) {
    const base = { type: q.type, instructions: textOf(q.instructions) };
    if (q.type === "noul") {
      out[id] = q.criteria && (q.criteria.true || q.criteria.false)
        ? { ...base, criteria: { true: textOf(q.criteria.true ?? "Yes"), false: textOf(q.criteria.false ?? "No") } }
        : base;
    } else if (q.type === "choice") {
      out[id] = { ...base, criteria: Object.fromEntries(Object.entries(q.criteria).map(([k, v]) => [k, v ?? k])) };
    } else {
      out[id] = { ...base, criteria: q.criteria };
    }
  }
  return out;
}

function optionKeys(q: JevQuestion): string[] {
  if (q.type === "noul") return ["true", "false"];
  if (q.type === "choice") return Object.keys(q.criteria);
  return q.criteria.map((_, i) => String(i));
}

function normalise(q: JevQuestion, probs: Record<string, number>): NormAnswer {
  const keys = optionKeys(q);
  const p: Record<string, number> = {};
  for (const k of keys) p[k] = probs[k] ?? 0;
  const top = keys.reduce((a, b) => (p[b]! > p[a]! ? b : a));
  const answer: NormAnswer = { top, probs: p };
  if (q.type === "noul") answer.pTrue = p.true;
  if (q.type === "score") answer.expected = keys.reduce((s, k) => s + Number(k) * p[k]!, 0);
  return answer;
}

async function sleep(ms: number): Promise<void> {
  const { promise, resolve } = Promise.withResolvers<void>();
  setTimeout(resolve, ms);
  return promise;
}

type Fetched<T> = { status: number; data?: T; latencyMs: number; error?: string };

const errorBody = z.object({ error: z.object({ message: z.string().optional() }).optional() });

/** POST with bounded retries for transport failures, 425, 429 and 503 (honours Retry-After). */
async function postJson<S extends z.ZodType>(
  url: string, headers: Record<string, string>, body: JsonValue, schema: S, timeoutMs = 300_000,
): Promise<Fetched<z.output<S>>> {
  let last: Fetched<z.output<S>> = { status: 0, latencyMs: 0, error: "not attempted" };
  for (let attempt = 0; attempt < 5; attempt++) {
    const started = performance.now();
    try {
      const res = await fetch(url, {
        method: "POST", headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify(body), signal: AbortSignal.timeout(timeoutMs),
      });
      const latencyMs = performance.now() - started;
      if (res.ok) {
        const parsed = schema.safeParse(await res.json());
        if (!parsed.success) return { status: res.status, latencyMs, error: "unexpected response shape" };
        return { status: res.status, data: parsed.data, latencyMs };
      }
      // Only the status and a short validation hint, never the echoed input.
      let hint = "";
      try { hint = (errorBody.parse(await res.json()).error?.message ?? "").slice(0, 160); } catch { /* no body */ }
      last = { status: res.status, latencyMs, error: `HTTP ${res.status}${hint ? `: ${hint}` : ""}` };
      // GLiDE intermittently answers `404 vLLM HTTP 404` (model_not_found) for requests that succeed on retry.
      if (!RETRYABLE.has(res.status) && !(res.status === 404 && hint.includes("vLLM"))) return last;
      const retryAfter = Number(res.headers.get("retry-after"));
      await sleep(Number.isFinite(retryAfter) && retryAfter > 0 ? Math.min(retryAfter, 90) * 1000 : 2000 * 2 ** attempt);
    } catch (cause) {
      last = { status: 0, latencyMs: performance.now() - started, error: cause instanceof Error ? cause.name : "transport error" };
      await sleep(2000 * 2 ** attempt);
    }
  }
  return last;
}

export function jevBackend(apiKey: string): Backend {
  return {
    id: "jev",
    async call(state, questions) {
      const r = await jevDecide({ kind: "key", key: apiKey }, state, questions, { timeoutMs: 120_000 });
      if (!r.ok) return { ok: false, error: r.error, httpStatus: r.httpStatus, latencyMs: r.latencyMs, model: r.resolvedModel };
      const answers: Record<string, NormAnswer> = {};
      for (const [id, q] of Object.entries(questions)) {
        const a = r.answers[id]!;
        answers[id] = normalise(q, a.type === "noul" ? { true: a.noul, false: 1 - a.noul } : a.probabilities);
      }
      return { ok: true, latencyMs: r.latencyMs, inputTokens: r.inputTokens, costUsd: r.costUsd, model: r.resolvedModel, answers };
    },
  };
}

const glideResponse = z.object({
  model: z.string().optional(),
  answers: z.record(z.string(), z.object({
    type: z.string(),
    noul: z.number().optional(),
    probabilities: z.record(z.string(), z.number()).optional(),
  })).optional(),
  usage: z.object({ input_tokens: z.number().optional() }).optional(),
});

export function glideBackend(apiKey: string): Backend {
  return {
    id: "glide",
    async call(state, questions) {
      const r = await postJson(`${FASTINO}/v1/systemone`, { "X-API-Key": apiKey },
        { model: GLIDE_MODEL, state, questions: toGlideQuestions(questions) }, glideResponse);
      const body = r.data;
      if (!body) return { ok: false, error: r.error, httpStatus: r.status, latencyMs: r.latencyMs };
      if (body.model !== "glide") return { ok: false, error: `unexpected model ${String(body.model).slice(0, 40)}`, latencyMs: r.latencyMs };
      const answers: Record<string, NormAnswer> = {};
      for (const [id, q] of Object.entries(questions)) {
        const a = body.answers?.[id];
        if (!a || a.type !== q.type) return { ok: false, error: `missing or mismatched answer ${id}`, latencyMs: r.latencyMs };
        const probs = q.type === "noul" && a.noul !== undefined
          ? { true: a.noul, false: 1 - a.noul }
          : a.probabilities;
        if (!probs) return { ok: false, error: `missing or mismatched answer ${id}`, latencyMs: r.latencyMs };
        answers[id] = normalise(q, probs);
      }
      const inputTokens = body.usage?.input_tokens;
      return { ok: true, latencyMs: r.latencyMs, inputTokens, model: body.model,
        costUsd: inputTokens === undefined ? undefined : inputTokens * GLIDE_PRICE / 1e6, answers };
    },
  };
}

export type DecideLabels = "described" | "keys";

function humanise(key: string): string { return key.replace(/_/g, " "); }

type DecideTask = { task: string; labels: string[]; keys: string[] };

/** Decide hosts no instructions or label descriptions: the task name and label strings carry the question. */
function decideTask(id: string, q: JevQuestion, mode: DecideLabels): DecideTask {
  const keys = optionKeys(q);
  let labels: string[];
  if (q.type === "noul") {
    labels = mode === "keys" || !q.criteria ? ["yes", "no"] : [textOf(q.criteria.true ?? "yes"), textOf(q.criteria.false ?? "no")];
  } else if (q.type === "choice") {
    labels = keys.map(k => mode === "keys" ? humanise(k) : textOf(q.criteria[k] ?? humanise(k)));
  } else {
    labels = q.criteria.map(String);
  }
  // Labels must be unique; disambiguate rather than drop.
  labels = labels.map((l, i) => labels.indexOf(l) === i ? l : `${l} (${keys[i]})`);
  return { task: mode === "keys" ? humanise(id) : textOf(q.instructions), labels, keys };
}

type ClippedState = { text: string; clipped: number };

function clipState(state: JsonValue, limit: number): ClippedState {
  const asString = z.string().safeParse(state);
  const text = asString.success ? asString.data : JSON.stringify(state, null, 1);
  if (text.length <= limit) return { text, clipped: 0 };
  const head = Math.floor(limit * 0.8);
  const tail = limit - head;
  return { text: `${text.slice(0, head)}\n[... ${text.length - limit} chars omitted ...]\n${text.slice(-tail)}`, clipped: text.length - limit };
}

const decideResponse = z.object({
  model: z.string().optional(),
  choices: z.array(z.object({ message: z.object({ content: z.string().optional() }).optional() })).optional(),
  usage: z.object({ total_tokens: z.number().optional() }).optional(),
});
const decideContent = z.record(z.string(), z.object({ label: z.string().optional(), confidence: z.number().optional() }));

function parseDecideContent(raw: string) {
  try {
    const parsed = decideContent.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : undefined;
  } catch {
    return undefined;
  }
}

export function decideBackend(apiKey: string, mode: DecideLabels): Backend {
  return {
    id: mode === "keys" ? "decide-keys" : "decide",
    async call(state, questions) {
      const tasks = Object.entries(questions).map(([id, q]) => ({ id, ...decideTask(id, q, mode) }));
      let limit = DECIDE_STATE_CHARS;
      for (let attempt = 0; ; attempt++) {
        const { text, clipped } = clipState(state, limit);
        const r = await postJson(`${FASTINO}/v1/chat/completions`, { Authorization: `Bearer ${apiKey}` }, {
          model: DECIDE_MODEL, store: false, messages: [{ role: "user", content: text }],
          schema: { classifications: tasks.map(t => ({ task: t.task, labels: t.labels, multi_label: false })) },
        }, decideResponse);
        if (r.status === 400 && attempt < 3 && /too long|token|context|length/i.test(r.error ?? "")) { limit = Math.floor(limit / 2); continue; }
        const body = r.data;
        if (!body) return { ok: false, error: r.error, httpStatus: r.status, latencyMs: r.latencyMs, clippedChars: clipped };
        const parsedContent = parseDecideContent(body.choices?.[0]?.message?.content ?? "");
        if (!parsedContent) return { ok: false, error: "unparseable content", latencyMs: r.latencyMs, clippedChars: clipped };
        const content = parsedContent;
        const answers: Record<string, NormAnswer> = {};
        for (const t of tasks) {
          const got = content[t.task];
          const idx = got?.label === undefined ? -1 : t.labels.indexOf(got.label);
          const confidence = got?.confidence;
          if (idx < 0 || confidence === undefined) {
            return { ok: false, error: `missing or unknown label for ${t.id}`, latencyMs: r.latencyMs, clippedChars: clipped };
          }
          // Single-label mode returns only the winner's confidence; spread the remainder evenly
          // (exact for two-option questions if confidence is a softmax probability).
          const rest = (1 - confidence) / (t.keys.length - 1);
          const probs = Object.fromEntries(t.keys.map((k, i) => [k, i === idx ? confidence : rest]));
          answers[t.id] = normalise(questions[t.id]!, probs);
        }
        const tokens = body.usage?.total_tokens;
        return { ok: true, latencyMs: r.latencyMs, model: body.model, clippedChars: clipped, inputTokens: tokens,
          costUsd: tokens === undefined ? undefined : tokens * DECIDE_PRICE / 1e6, answers };
      }
    },
  };
}
