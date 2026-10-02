#!/usr/bin/env bun
// Replay stored jev-scope review states through the CURRENT watchdog questions, review policy
// (agent/WATCHDOG.yml) and composition, and write one evaluate-jev case per input, so labels can be
// swept. Run it in a freshly started process: the code under test is whatever is on disk now.
// Usage: bun jev-lab/scripts/replay-scope.ts --cases-in <export.jsonl> --out <replayed.jsonl>
//          [--ids <ids.json>] [--concurrency 6]
// --cases-in comes from `export-cases.ts --source jev-scope`; --ids limits the replay to a JSON array of
// case ids (for example a labelled sample). Case ids are kept so labels still join.
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { z } from "zod";
import { composeScope, hiddenActivity, POLICY_VERSION, QUESTIONS } from "../../agent/extensions/jev-watchdog.ts";
import { loadJevApiKey } from "../../agent/extensions/lib/jev-auth.ts";
import { decide, type JsonValue } from "../../agent/extensions/lib/jev.ts";

const args = new Map<string, string>();
for (let i = 2; i < process.argv.length; i++) {
  const key = (process.argv[i] ?? "").replace(/^--/, "");
  const next = process.argv[i + 1];
  if (next === undefined || next.startsWith("--")) args.set(key, "");
  else { args.set(key, next); i++; }
}
const casesIn = args.get("cases-in");
const out = args.get("out");
if (!casesIn || !out) throw new Error("--cases-in and --out are required");
const apiKey = await loadJevApiKey();
if (!apiKey) throw new Error("No Jev credential available.");

const watchdogConfig = z.object({
  advisors: z.array(z.object({ name: z.string(), instructions: z.string().optional() })),
});
const config = watchdogConfig.parse(Bun.YAML.parse(await readFile(join(import.meta.dir, "../../agent/WATCHDOG.yml"), "utf8")));
const reviewPolicy = config.advisors.find((advisor) => advisor.name === "jev-scope")?.instructions?.trim();
if (!reviewPolicy) throw new Error("agent/WATCHDOG.yml has no jev-scope instructions");

const storedState = z.object({ agent_activity: z.object({ excerpt: z.string() }).loose() }).loose();
const inputCase = z.object({
  type: z.literal("case").optional(), id: z.string(), version: z.string().optional(), state: storedState.nullable().optional(),
  label: z.string().nullable().optional(), labelBy: z.string().nullable().optional(),
});
type InputCase = z.infer<typeof inputCase>;
const scopeAnswer = z.object({ choice: z.string(), probabilities: z.record(z.string(), z.number()), confidence: z.number() });
type Outcome = { verdict: string; score?: number; state?: JsonValue; answers?: JsonValue; error?: string; costUsd?: number;
  latencyMs?: number; resolvedModel?: string };

const wanted = args.get("ids") ? new Set(z.array(z.string()).parse(JSON.parse(await readFile(args.get("ids") ?? "", "utf8")))) : undefined;
const inputs: InputCase[] = [];
for (const line of (await readFile(casesIn, "utf8")).split("\n")) {
  if (!line.includes("\"case\"")) continue;
  const parsed = inputCase.safeParse(JSON.parse(line));
  if (parsed.success && parsed.data.state && (!wanted || wanted.has(parsed.data.id))) inputs.push(parsed.data);
}

const jsonValue: z.ZodType<JsonValue> = z.lazy(() =>
  z.union([z.string(), z.number(), z.boolean(), z.null(), z.array(jsonValue), z.record(z.string(), jsonValue)]));
const jsonState = z.record(z.string(), jsonValue);

async function replay(input: InputCase): Promise<Outcome> {
  if (!input.state) return { verdict: "no-outcome", error: "stored case has no state" };
  const activityHidden = hiddenActivity(input.state.agent_activity.excerpt);
  const state: JsonValue = { ...jsonState.parse(input.state), policy_version: POLICY_VERSION, review_policy: reviewPolicy ?? "",
    activity_hidden: activityHidden };
  const result = await decide(apiKey ?? "", state, QUESTIONS, { timeoutMs: 30_000 });
  if (!result.ok) return { verdict: "error", error: result.error, latencyMs: result.latencyMs };
  const components = z.record(z.string(), scopeAnswer).parse(result.answers);
  const headline = composeScope(components, activityHidden);
  return { verdict: headline.choice, score: headline.probabilities.yes ?? 0, state, answers: result.answers,
    costUsd: result.costUsd, latencyMs: result.latencyMs, resolvedModel: result.resolvedModel };
}

const results: Outcome[] = Array.from({ length: inputs.length }, (): Outcome => ({ verdict: "no-outcome" }));
let next = 0;
await Promise.all(Array.from({ length: Number(args.get("concurrency") ?? 6) }, async () => {
  while (next < inputs.length) {
    const index = next++;
    const item = inputs[index];
    if (!item) continue;
    try {
      results[index] = await replay(item);
    } catch (error) {
      results[index] = { verdict: "error", error: error instanceof Error ? error.message : String(error) };
    }
  }
}));

const lines = inputs.map((item, index) => {
  const outcome = results[index] ?? { verdict: "no-outcome" };
  return JSON.stringify({
    type: "case", id: item.id, timestamp: new Date().toISOString(), version: POLICY_VERSION, replayOf: item.version,
    stage: outcome.answers ? "jev" : "jev_error", state: outcome.state, answers: outcome.answers, verdict: outcome.verdict,
    score: outcome.score, label: item.label ?? undefined, labelBy: item.labelBy ?? undefined,
    labelOptions: ["overreach", "no_overreach", "uncertain"], positiveLabel: "overreach", error: outcome.error,
    costUsd: outcome.costUsd, latencyMs: outcome.latencyMs, resolvedModel: outcome.resolvedModel,
  });
});
await writeFile(out, lines.join("\n") + "\n", { mode: 0o600 });
const verdicts: Record<string, number> = {};
for (const outcome of results) verdicts[outcome.verdict] = (verdicts[outcome.verdict] ?? 0) + 1;
console.error(`replayed ${inputs.length} jev-scope cases with ${POLICY_VERSION} -> ${out}: ${JSON.stringify(verdicts)}`);
