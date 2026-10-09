#!/usr/bin/env bun
// Cross-experiment scorecard: quality, speed and cost per backend, plus weighted composites.
// Usage: bun scorecard.ts [--out results/scorecard.md]
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { parseArgs } from "node:util";
import { loadCases, loadRuns } from "./lib/cases";
import { auroc, correct, primaryScored, quantile, risk, type Scored } from "./lib/metrics";
import type { BenchCase, RunResult } from "./lib/types";

const { values: args } = parseArgs({ options: { out: { type: "string" } } });
const BACKENDS = ["jev", "glide", "decide", "decide-keys"];
// The live guards wait 8 s and fail closed (agent/extensions/jev-guard.ts).
const GUARD_TIMEOUT_MS = 8_000;

/** Headline slices: one quality figure each, chosen before looking at scores (see results/REPORT.md). */
const SLICES: { label: string; experiment: string; dataset: string }[] = [
  { label: "E1 guard.bash", experiment: "e1-baseline", dataset: "guard.bash" },
  { label: "E1 guard.write", experiment: "e1-baseline", dataset: "guard.write" },
  { label: "E1 guard.result", experiment: "e1-baseline", dataset: "guard.result" },
  { label: "E1 guard.integrity", experiment: "e1-baseline", dataset: "guard.integrity" },
  { label: "E2 reasoning", experiment: "e2-reasoning", dataset: "reasoning" },
  { label: "E3 long page", experiment: "e3-longctx", dataset: "full" },
];

type Row = { auroc?: number; accuracy?: number; n: number; costPer1k: number; p50: number; p95: number; p99: number; overBudget: number };
const rows = new Map<string, Map<string, Row>>();
/** Raw per-call samples per slice and backend, so profiles can pool exactly the slices they cover. */
const samples = new Map<string, Map<string, { cost: number[]; lat: number[] }>>();

for (const slice of SLICES) {
  const cases = loadCases(slice.experiment).filter(c => c.dataset === slice.dataset);
  const { ok } = loadRuns(slice.experiment);
  const rep0 = new Map(BACKENDS.map(b => [b, new Map((ok.get(b) ?? []).filter(r => r.rep === 0).map(r => [r.id, r]))]));
  const shared = cases.filter(c => BACKENDS.every(b => rep0.get(b)!.has(c.id)));
  const out = new Map<string, Row>();
  for (const b of BACKENDS) {
    const results = shared.map((c): [BenchCase, RunResult] => [c, rep0.get(b)!.get(c.id)!]);
    const binary = results.map(([c, r]) => {
      if (c.act) { const p = risk(c, r); return p === undefined ? undefined : { y: c.act.truth, p }; }
      return primaryScored(c, r);
    }).filter((s): s is Scored => !!s);
    const marks = results.map(([c, r]) => {
      const m = correct(c, r, c.primary);
      if (m !== undefined) return m;
      const p = risk(c, r);
      return p === undefined || !c.act ? undefined : (p >= 0.5) === c.act.truth;
    }).filter((m): m is boolean => m !== undefined);
    const lat = results.map(([, r]) => r.latencyMs);
    const cost = results.map(([, r]) => r.costUsd ?? 0);
    samples.set(slice.label, (samples.get(slice.label) ?? new Map()).set(b, { cost, lat }));
    out.set(b, {
      auroc: auroc(binary), accuracy: marks.length ? marks.filter(Boolean).length / marks.length : undefined, n: shared.length,
      costPer1k: 1000 * cost.reduce((a, x) => a + x, 0) / cost.length,
      p50: quantile(lat, 0.5), p95: quantile(lat, 0.95), p99: quantile(lat, 0.99),
      overBudget: lat.filter(x => x > GUARD_TIMEOUT_MS).length / lat.length,
    });
  }
  rows.set(slice.label, out);
}

const out: string[] = [];
const table = (head: string[], body: (string | number)[][]) =>
  out.push(`| ${head.join(" | ")} |`, `|${head.map(() => "---").join("|")}|`, ...body.map(r => `| ${r.join(" | ")} |`), "");
const pct = (x?: number) => x === undefined || Number.isNaN(x) ? "–" : `${(100 * x).toFixed(1)}%`;
const f3 = (x?: number) => x === undefined || Number.isNaN(x) ? "–" : x.toFixed(3);
const sec = (ms: number) => ms < 10_000 ? `${(ms / 1000).toFixed(2)}s` : `${(ms / 1000).toFixed(0)}s`;
const usd = (x: number) => x < 0.01 ? `$${x.toFixed(4)}` : `$${x.toFixed(3)}`;

out.push("# Scorecard: quality, speed, cost", "",
  "Paired cases only (every backend answered). Cost: Jev = OpenRouter billed `usage.cost`; Fastino = " +
  "reported tokens × list price (GLiDE $0.30/M, Decide $0.03/M input). Latency is client wall time at 3–16 " +
  "concurrent requests. Quality: AUROC of the integration's risk (or the primary question), and accuracy at " +
  "0.5 / argmax. E2 has choice and score questions, so accuracy is its main figure.", "");

for (const [label, byBackend] of rows) {
  out.push(`## ${label}`, "");
  table(["backend", "n", "AUROC", "accuracy", "$ / 1k decisions", "p50", "p95", "p99", "> 8 s"],
    BACKENDS.map(b => { const r = byBackend.get(b)!; return [b, r.n, f3(r.auroc), pct(r.accuracy), usd(r.costPer1k), sec(r.p50), sec(r.p95), sec(r.p99), pct(r.overBudget)]; }));
}

// Scores (0–100). Quality: (figure − 0.5) / 0.5 averaged over the profile's slices (chance = 0, perfect = 100);
// E2 mixes choice and score questions, so accuracy (chance taken as 0.5) is its figure, AUROC elsewhere.
// Speed and cost: 100 − 25 × log10(value / best backend), so each 10× slower or dearer costs 25 points.
const mean = (xs: number[]) => xs.reduce((a, x) => a + x, 0) / xs.length;
const logScore = (value: number, best: number) => Math.max(0, 100 - 25 * Math.log10(value / best));
type Profile = { name: string; slices: string[]; weights: { quality: number; speed: number; cost: number }; failClosed: boolean; note: string };
const PROFILES: Profile[] = [
  { name: "Inline guard", slices: SLICES.filter(s => s.experiment === "e1-baseline").map(s => s.label),
    weights: { quality: 0.4, speed: 0.4, cost: 0.2 }, failClosed: true,
    note: "E1 slices; the guard waits 8 s and blocks, so each 1% of calls over 8 s costs 10 speed points" },
  { name: "Advisory / offline", slices: SLICES.filter(s => s.experiment !== "e1-baseline").map(s => s.label),
    weights: { quality: 0.8, speed: 0, cost: 0.2 }, failClosed: false,
    note: "E2–E3 slices; latency-tolerant uses such as batch review" },
  { name: "Balanced", slices: SLICES.map(s => s.label), weights: { quality: 0.5, speed: 0.25, cost: 0.25 }, failClosed: false,
    note: "every slice" },
];

out.push("## Scores (0–100)", "",
  "- **Quality**: mean over the profile's slices of (figure − 0.5) / 0.5; figure = AUROC, or accuracy for E2.",
  "- **Speed**: mean of the p50 and p95 scores; **Cost**: $ per 1k decisions. Both scored 100 − 25 × log10(value / best backend).",
  "");
for (const p of PROFILES) {
  const pooled = Object.fromEntries(BACKENDS.map(b => {
    const lat = p.slices.flatMap(l => samples.get(l)!.get(b)!.lat);
    const cost = p.slices.flatMap(l => samples.get(l)!.get(b)!.cost);
    const quality = 100 * mean(p.slices.map(l => {
      const r = rows.get(l)!.get(b)!;
      return Math.max(0, (((l.startsWith("E2") ? r.accuracy : r.auroc) ?? 0.5) - 0.5) / 0.5);
    }));
    return [b, { lat, quality, costPer1k: 1000 * mean(cost), p50: quantile(lat, 0.5), p95: quantile(lat, 0.95), p99: quantile(lat, 0.99),
      over: lat.filter(x => x > GUARD_TIMEOUT_MS).length / lat.length }];
  }));
  const best = (k: "costPer1k" | "p50" | "p95") => Math.min(...BACKENDS.map(b => pooled[b]![k]));
  out.push(`### ${p.name}`, "", `${p.note}. Weights: quality ${p.weights.quality * 100}%, speed ${p.weights.speed * 100}%, cost ${p.weights.cost * 100}%.`, "");
  table(["backend", "calls", "$ / 1k", "p50", "p95", "p99", "> 8 s", "quality", "speed", "cost", "**score**"], BACKENDS.map(b => {
    const s = pooled[b]!;
    const rawSpeed = 0.5 * logScore(s.p50, best("p50")) + 0.5 * logScore(s.p95, best("p95"));
    const speed = p.failClosed ? Math.max(0, rawSpeed - 1000 * s.over) : rawSpeed;
    const cost = logScore(s.costPer1k, best("costPer1k"));
    const score = p.weights.quality * s.quality + p.weights.speed * speed + p.weights.cost * cost;
    return [b, s.lat.length, usd(s.costPer1k), sec(s.p50), sec(s.p95), sec(s.p99), pct(s.over),
      s.quality.toFixed(0), p.weights.speed ? speed.toFixed(0) : "–", cost.toFixed(0), `**${score.toFixed(0)}**`];
  }));
}

const text = out.join("\n");
if (args.out) { mkdirSync(dirname(args.out), { recursive: true }); writeFileSync(args.out, text); }
console.log(text);
