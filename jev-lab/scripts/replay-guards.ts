#!/usr/bin/env bun
// Replay stored guard states through the CURRENT questions, deterministic rules and composition, and
// write one evaluate-jev case per input with the new answers and verdict, so labels can be swept.
// Run it in a freshly started process (the code under test is whatever is on disk now).
// Usage: bun jev-lab/scripts/replay-guards.ts --policy <bash|write|result> --cases-in <export.jsonl>
//          --out <replayed.jsonl> [--concurrency 6] [--limit N]
// --cases-in comes from `export-cases.ts --source policy:guard.<name>`; case ids are kept so existing
// labels still join (the replayed case records the version it came from in `replayOf`).
// Prints per-stage counts, and per-stage precision against labels carried on the input cases.
import { readFile, writeFile } from "node:fs/promises";
import { z } from "zod";
import {
  assessBash, assessRecoverable, assessResult, assessWrite, BASH_POLICY_VERSION, BASH_QUESTIONS, bashJevState, classifyBash, isSecretPath,
  needsSecretJudgement, PRIVATE_KEY_HEADER, RESULT_POLICY_VERSION, RESULT_QUESTIONS, resultWindows, stripHarnessText,
  WRITE_POLICY_VERSION, WRITE_QUESTIONS, writeJevState,
} from "../../agent/extensions/jev-guard.ts";
import { loadJevApiKey } from "../../agent/extensions/lib/jev-auth.ts";
import { decide, type JevAnswers, type JevQuestions, type JsonValue } from "../../agent/extensions/lib/jev.ts";

const POLICIES = {
  bash: { policy: "guard.bash", version: BASH_POLICY_VERSION, questions: BASH_QUESTIONS },
  write: { policy: "guard.write", version: WRITE_POLICY_VERSION, questions: WRITE_QUESTIONS },
  result: { policy: "guard.result", version: RESULT_POLICY_VERSION, questions: RESULT_QUESTIONS },
} as const;
const RESULT_WINDOW_CONCURRENCY = 4;

const inputCase = z.object({
  type: z.literal("case").optional(), id: z.string(), version: z.string().optional(), subject: z.string().optional(),
  state: z.record(z.string(), z.unknown()).nullable().optional(), label: z.string().nullable().optional(),
  labelBy: z.string().nullable().optional(), labelOptions: z.array(z.string()).optional(),
});
type InputCase = z.infer<typeof inputCase>;
type Outcome = { stage: string; verdict: string; rule?: string; state?: JsonValue; answers?: JevAnswers; score?: number; error?: string;
  costUsd?: number; latencyMs?: number; resolvedModel?: string };
type Asked = { answers?: JevAnswers; error?: string; costUsd?: number; latencyMs?: number; resolvedModel?: string };

const args = new Map<string, string>();
for (let i = 2; i < process.argv.length; i++) {
  const key = (process.argv[i] ?? "").replace(/^--/, "");
  const next = process.argv[i + 1];
  if (next === undefined || next.startsWith("--")) args.set(key, "");
  else { args.set(key, next); i++; }
}
const name = args.get("policy");
if (name !== "bash" && name !== "write" && name !== "result") throw new Error("--policy must be bash, write or result");
const spec = POLICIES[name];
const casesIn = args.get("cases-in");
const out = args.get("out");
if (!casesIn || !out) throw new Error("--cases-in and --out are required");
const apiKey = await loadJevApiKey();
if (!apiKey) throw new Error("No Jev credential available.");

async function ask(state: JsonValue, questions: JevQuestions): Promise<Asked> {
  const result = await decide(apiKey ?? "", state, questions);
  return result.ok ? { answers: result.answers, costUsd: result.costUsd, latencyMs: result.latencyMs, resolvedModel: result.resolvedModel }
    : { error: result.error, latencyMs: result.latencyMs };
}

const bashState = z.object({ command: z.string().default(""), cwd: z.string().default("") });
const writeState = z.object({ path: z.string().default(""), added_excerpt: z.string().default("") });
const resultState = z.object({ source: z.string().default(""), text: z.string().default("") });

async function replayBash(state: z.infer<typeof bashState>): Promise<Outcome> {
  const command = state.command;
  const verdict = classifyBash(command);
  if (verdict.kind === "deny") return { stage: "deterministic", verdict: "block", rule: verdict.rule };
  if (verdict.kind === "confirm") return { stage: "deterministic", verdict: "confirm", rule: verdict.rule };
  if (verdict.kind === "allow") return { stage: "deterministic", verdict: "allow", rule: "allowlist" };
  // No session history exists in a replay, so only the cwd's current git state and the command text decide.
  const recoverable = await assessRecoverable(command, state.cwd || process.cwd(), new Set());
  if (recoverable.qualifies) return { stage: "deterministic", verdict: "allow", rule: "project-recoverable", state: recoverable.targets };
  const built = await bashJevState(command, state.cwd || process.cwd(), apiKey, recoverable);
  const judged = await ask(built.state, BASH_QUESTIONS);
  if (!judged.answers) return { stage: "jev_error", verdict: "error", state: built.state, ...judged };
  const assessed = assessBash(judged.answers, built.facts);
  const exposure = judged.answers.secret_exposure;
  const effect = judged.answers.effect;
  const destructive = judged.answers.destructive;
  const score = Math.max(effect?.type === "choice" ? (effect.probabilities.irreversible ?? 0) : 0,
    destructive?.type === "noul" ? destructive.noul : 0,
    built.facts.secret_value_possible && exposure?.type === "noul" ? exposure.noul : 0);
  return { stage: "jev", verdict: assessed.verdict, state: built.state, score, ...judged };
}

async function replayWrite(state: z.infer<typeof writeState>): Promise<Outcome> {
  const path = state.path;
  const added = state.added_excerpt;
  if (isSecretPath(path)) return { stage: "deterministic", verdict: "block", rule: "secret-file" };
  if (PRIVATE_KEY_HEADER.test(added)) return { stage: "deterministic", verdict: "block", rule: "secret-literal" };
  if (!needsSecretJudgement(added)) return { stage: "deterministic", verdict: "allow", rule: "prefilter-no-match" };
  // Stored excerpts have known-format keys already masked, so their format facts cannot be rebuilt.
  const built = writeJevState(path, added, apiKey);
  const judged = await ask(built, WRITE_QUESTIONS);
  if (!judged.answers) return { stage: "jev_error", verdict: "error", state: built, ...judged };
  const secret = judged.answers.contains_secret;
  return { stage: "jev", verdict: assessWrite(judged.answers).verdict, state: built, score: secret?.type === "noul" ? secret.noul : 0, ...judged };
}

async function replayResult(state: z.infer<typeof resultState>): Promise<Outcome> {
  const source = state.source;
  const cleaned = stripHarnessText(state.text);
  if (cleaned.length < 40) return { stage: "deterministic", verdict: "allow", rule: "too-short" };
  const { windows, total } = resultWindows(cleaned);
  const judged: (Asked & { state: { source: string; text: string }; score: number })[] = [];
  for (let i = 0; i < windows.length; i += RESULT_WINDOW_CONCURRENCY) {
    judged.push(...await Promise.all(windows.slice(i, i + RESULT_WINDOW_CONCURRENCY).map(async (window) => {
      const windowState = { source, text: window };
      const answered = await ask(windowState, RESULT_QUESTIONS);
      const answer = answered.answers?.prompt_injection;
      return { ...answered, state: windowState, score: answer?.type === "noul" ? answer.noul : -1 };
    })));
  }
  const best = judged.reduce((top, next) => (next.score > top.score ? next : top));
  const recorded = { ...best.state, windows_total: total, windows_judged: windows.length, window_scores: judged.map((w) => (w.score < 0 ? null : w.score)) };
  if (!best.answers) return { stage: "jev_error", verdict: "error", state: recorded, error: best.error };
  return { stage: "jev", verdict: assessResult(best.answers).verdict, state: recorded, answers: best.answers, score: best.score,
    costUsd: judged.reduce((sum, w) => sum + (w.costUsd ?? 0), 0), latencyMs: best.latencyMs, resolvedModel: best.resolvedModel };
}

async function replay(input: InputCase): Promise<Outcome> {
  const state = input.state;
  if (!state) return { stage: "no_outcome", verdict: "no-outcome", error: "stored case has no state" };
  try {
    if (name === "bash") return await replayBash(bashState.parse(state));
    return name === "write" ? await replayWrite(writeState.parse(state)) : await replayResult(resultState.parse(state));
  } catch (error) {
    return { stage: "jev_error", verdict: "error", error: error instanceof Error ? error.message : String(error) };
  }
}

const inputs: InputCase[] = [];
for (const line of (await readFile(casesIn, "utf8")).split("\n")) {
  if (!line.includes("\"case\"")) continue;
  const parsed = inputCase.safeParse(JSON.parse(line));
  if (parsed.success) inputs.push(parsed.data);
}
const limit = Number(args.get("limit") ?? inputs.length);
const todo = inputs.slice(0, limit);
const results: Outcome[] = Array.from({ length: todo.length }, (): Outcome => ({ stage: "no_outcome", verdict: "no-outcome" }));
let next = 0;
const workers = Array.from({ length: Number(args.get("concurrency") ?? 6) }, async () => {
  while (next < todo.length) {
    const index = next++;
    const item = todo[index];
    if (item) results[index] = await replay(item);
  }
});
await Promise.all(workers);

const lines = todo.map((item, index) => {
  const outcome = results[index];
  if (!outcome) return "";
  const answers = outcome.answers ? Object.entries(outcome.answers).map(([id, answer]) => ({ id, ...answer })) : undefined;
  return JSON.stringify({
    type: "case", id: item.id, timestamp: new Date().toISOString(), version: spec.version, replayOf: item.version, stage: outcome.stage,
    subject: item.subject, state: outcome.state, questions: outcome.answers ? spec.questions : undefined, answers, verdict: outcome.verdict,
    score: outcome.score, rule: outcome.rule, label: item.label ?? undefined, labelBy: item.labelBy ?? undefined,
    labelOptions: item.labelOptions, error: outcome.error, costUsd: outcome.costUsd, latencyMs: outcome.latencyMs, resolvedModel: outcome.resolvedModel,
  });
}).filter(Boolean);
await writeFile(out, lines.join("\n") + "\n", { mode: 0o600 });

// Per-stage report: where decisions come from, and how often each stage's non-allow verdicts are right.
type Tally = { total: number; verdicts: Record<string, number>; flaggedLabelled: number; flaggedCorrect: number; passedLabelled: number; passedMissed: number };
const stages = new Map<string, Tally>();
todo.forEach((item, index) => {
  const outcome = results[index];
  if (!outcome) return;
  const tally = stages.get(outcome.stage) ?? { total: 0, verdicts: {}, flaggedLabelled: 0, flaggedCorrect: 0, passedLabelled: 0, passedMissed: 0 };
  tally.total++;
  tally.verdicts[outcome.verdict] = (tally.verdicts[outcome.verdict] ?? 0) + 1;
  const flagged = outcome.verdict !== "allow" && outcome.verdict !== "error" && outcome.verdict !== "no-outcome";
  if (item.label) {
    if (flagged) { tally.flaggedLabelled++; if (item.label === "correct") tally.flaggedCorrect++; }
    else if (outcome.verdict === "allow") { tally.passedLabelled++; if (item.label === "false_negative") tally.passedMissed++; }
  }
  stages.set(outcome.stage, tally);
});
console.error(`replayed ${todo.length} ${name} cases with ${spec.version} -> ${out}`);
for (const [stage, tally] of stages) {
  const precision = tally.flaggedLabelled > 0 ? `${tally.flaggedCorrect}/${tally.flaggedLabelled}` : "n/a";
  console.error(`stage ${stage}: ${tally.total} cases ${JSON.stringify(tally.verdicts)}; labelled non-allow correct ${precision}; ` +
    `labelled allows that were false negatives ${tally.passedMissed}/${tally.passedLabelled}`);
}
