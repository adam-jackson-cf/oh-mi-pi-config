#!/usr/bin/env bun
// Summarize Jev decision cases in the portable case format (references/case-format.md).
// Needs only `zod`: run with `bun summarize.ts …` (Bun installs it on first use) or with Node >= 23.6 where zod is installed.
// Usage: summarize.ts --cases-in <cases.jsonl> [--labels-in <labels.jsonl>] [--version current|all|<v>]
//          [--since <ISO>] [--until <ISO>] [--threshold <0..1>] [--cases-out <out.jsonl>]
// Prints aggregates only; --cases-out writes the filtered cases with effective labels (mode 0600).
import { readFile, writeFile } from "node:fs/promises";
import { z } from "zod";
import {
  casesForVersion, computeMetrics, CURRENT_VERSION, foldLabel, headlineScore, versionsOf,
  type EffectiveLabel, type LabelBy, type MetricCase,
} from "./cases.ts";

const labelBy = z.enum(["human", "agent"]);
const answerLine = z.object({
  id: z.string().optional(), type: z.enum(["noul", "choice", "score"]).optional(), noul: z.number().optional(),
  choice: z.string().optional(), probabilities: z.record(z.string(), z.number()).optional(),
  score: z.number().optional(), confidence: z.number().optional(),
});
// Fields outside the case format are ignored and not copied to --cases-out.
const caseLine = z.object({
  type: z.literal("case").optional(), id: z.string(), verdict: z.string(),
  timestamp: z.string().optional(), version: z.string().optional(), stage: z.string().optional(), subject: z.string().optional(),
  state: z.json().optional(), questions: z.json().optional(), answers: z.array(answerLine).optional(), score: z.number().optional(),
  label: z.string().optional(), labelBy: labelBy.optional(), labelOptions: z.array(z.string()).optional(),
  positiveLabel: z.string().optional(), sufficient: z.boolean().optional(), group: z.string().optional(),
  evidence: z.string().optional(), resolvedModel: z.string().optional(), error: z.string().optional(),
  costUsd: z.number().optional(), latencyMs: z.number().optional(),
});
const failureLine = z.object({ type: z.literal("failure"), timestamp: z.string().optional(), reason: z.string().optional() });
const labelLine = z.object({ id: z.string(), label: z.string(), by: labelBy.optional() });

type Case = Omit<z.infer<typeof caseLine>, "label" | "labelBy"> & MetricCase;

const args = new Map<string, string>();
for (let i = 2; i < process.argv.length; i++) {
  const key = process.argv[i]!.replace(/^--/, "");
  const next = process.argv[i + 1];
  if (next === undefined || next.startsWith("--")) args.set(key, "");
  else { args.set(key, next); i++; }
}
const casesIn = args.get("cases-in");
if (!casesIn) {
  console.error("--cases-in <cases.jsonl> is required (format: references/case-format.md)");
  process.exit(2);
}
const version = args.get("version") || CURRENT_VERSION;
const since = args.get("since");
const until = args.get("until");
const threshold = Number(args.get("threshold") || 0.5);
const inWindow = (timestamp: string | undefined) =>
  (!since || (timestamp ?? "") >= since) && (!until || (timestamp ?? "") < until);

let malformed = 0;
const failures: Record<string, number> = {};
const loaded: Case[] = [];
for (const line of (await readFile(casesIn, "utf8")).split("\n")) {
  if (!line.trim()) continue;
  let raw: unknown;
  try { raw = JSON.parse(line); } catch { malformed++; continue; }
  const failure = failureLine.safeParse(raw);
  if (failure.success) {
    if (inWindow(failure.data.timestamp)) {
      const reason = failure.data.reason ?? "unknown";
      failures[reason] = (failures[reason] ?? 0) + 1;
    }
    continue;
  }
  const parsed = caseLine.safeParse(raw);
  if (!parsed.success) { malformed++; continue; }
  const { label, labelBy: by, ...rest } = parsed.data;
  loaded.push({ ...rest, label: label ?? null, labelBy: label === undefined ? null : by ?? "human",
    score: rest.score ?? headlineScore(rest.answers ?? []) });
}

const labelsIn = args.get("labels-in");
if (labelsIn) {
  const effective = new Map<string, EffectiveLabel>();
  for (const c of loaded) if (c.label !== null) effective.set(c.id, { label: c.label, by: c.labelBy ?? "human" });
  for (const line of (await readFile(labelsIn, "utf8")).split("\n")) {
    if (!line.trim()) continue;
    let raw: unknown;
    try { raw = JSON.parse(line); } catch { malformed++; continue; }
    const row = labelLine.safeParse(raw);
    if (!row.success) { malformed++; continue; }
    const by: LabelBy = row.data.by ?? "human";
    effective.set(row.data.id, foldLabel(effective.get(row.data.id), { label: row.data.label, by }));
  }
  for (const c of loaded) {
    const applied = effective.get(c.id);
    if (applied) { c.label = applied.label; c.labelBy = applied.by; }
  }
}

const versions = versionsOf(loaded);
const cases = (version === "all" ? loaded : casesForVersion(loaded, version)).filter(c => inWindow(c.timestamp));
const tally = (f: (c: Case) => string | undefined) =>
  cases.reduce<Record<string, number>>((m, c) => { const k = f(c) ?? "absent"; m[k] = (m[k] ?? 0) + 1; return m; }, {});
const scores = cases.map(c => c.score).filter((p): p is number => p !== undefined).sort((a, b) => a - b);
const unresolved = cases.filter(c => c.error !== undefined || c.stage === "jev_error" || c.stage === "no_outcome");
const withSufficiency = cases.filter(c => c.sufficient !== undefined);
const { sweep, ...metrics } = computeMetrics(cases, threshold);

console.log(JSON.stringify({
  casesIn,
  filters: { version, resolvedVersion: version === CURRENT_VERSION ? versions.current : version, since: since ?? null, until: until ?? null, threshold },
  versionsSeen: versions.versions,
  malformedLines: malformed,
  failuresBeforeCase: failures,
  byVersion: tally(c => c.version),
  byStage: tally(c => c.stage),
  byVerdict: tally(c => c.verdict),
  byResolvedModel: tally(c => c.resolvedModel),
  byGroup: tally(c => c.group),
  unresolved: { count: unresolved.length, byError: unresolved.reduce<Record<string, number>>((m, c) => {
    const k = c.error ?? c.verdict; m[k] = (m[k] ?? 0) + 1; return m;
  }, {}) },
  labels: { options: [...new Set(cases.flatMap(c => c.labelOptions ?? []))], byReviewer: tally(c => c.labelBy ?? "unlabelled") },
  inputSufficiency: withSufficiency.length ? { sufficient: withSufficiency.filter(c => c.sufficient).length, of: withSufficiency.length } : null,
  ...metrics,
  // Headline score: the first answer's noul, else its P(yes). Near 0.5 means yes and no are similarly likely.
  score: scores.length ? {
    n: scores.length, min: scores[0], median: scores[scores.length >> 1], max: scores.at(-1),
    atOrAboveThreshold: scores.filter(p => p >= threshold).length, coinFlip0_4to0_6: scores.filter(p => p >= 0.4 && p <= 0.6).length,
  } : null,
  sweep,
}, null, 2));

const out = args.get("cases-out");
if (out) {
  await writeFile(out, cases.map(c => JSON.stringify(c)).join("\n") + "\n", { mode: 0o600 });
  console.log(`cases written: ${out}`);
}
