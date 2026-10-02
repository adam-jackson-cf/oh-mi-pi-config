import type { LabCase } from "./types";

export type Confusion = { tp: number; fp: number; fn: number; tn: number; precision: number | null; recall: number | null };
export type SweepRow = Confusion & { threshold: number };

export type SourceMetrics = {
  total: number;
  labelCounts: Record<string, number>;
  unlabelled: number;
  positive?: string;
  /** Labelled cases with a score whose label is the positive or a definite negative (not `uncertain`). */
  scored: number;
  excludedUncertain: number;
  confusion?: Confusion;
  sweep: SweepRow[];
  verdictByLabel: Record<string, Record<string, number>>;
  cost: { calls: number; totalUsd: number; meanUsd: number | null };
  latency: { calls: number; meanMs: number | null; p50Ms: number | null; p95Ms: number | null };
};

const NEUTRAL_LABELS = ["uncertain", "unknown"];

function rate(numerator: number, denominator: number): number | null {
  return denominator === 0 ? null : numerator / denominator;
}

/** Confusion matrix: predicted positive when score >= threshold. */
export function confusionAt(cases: LabCase[], threshold: number, positive: string): Confusion {
  let tp = 0, fp = 0, fn = 0, tn = 0;
  for (const c of cases) {
    if (c.score === undefined || c.label === null || NEUTRAL_LABELS.includes(c.label)) continue;
    const predicted = c.score >= threshold;
    const actual = c.label === positive;
    if (predicted && actual) tp++;
    else if (predicted) fp++;
    else if (actual) fn++;
    else tn++;
  }
  return { tp, fp, fn, tn, precision: rate(tp, tp + fp), recall: rate(tp, tp + fn) };
}

/** Thresholds 0.50 .. 0.95 step 0.05 (integer stepping avoids float drift). */
export function thresholdSweep(cases: LabCase[], positive: string): SweepRow[] {
  return Array.from({ length: 10 }, (_, i) => {
    const threshold = (50 + i * 5) / 100;
    return { threshold, ...confusionAt(cases, threshold, positive) };
  });
}

function percentile(sorted: number[], q: number): number | null {
  if (sorted.length === 0) return null;
  return sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)] ?? null;
}

export function computeMetrics(cases: LabCase[], threshold: number): SourceMetrics {
  const positive = cases.find(c => c.positiveLabel)?.positiveLabel;
  const labelCounts: Record<string, number> = {};
  const verdictByLabel: Record<string, Record<string, number>> = {};
  for (const c of cases) {
    const key = c.label ?? "unlabelled";
    if (c.label !== null) labelCounts[c.label] = (labelCounts[c.label] ?? 0) + 1;
    const row = (verdictByLabel[c.verdict] ??= {});
    row[key] = (row[key] ?? 0) + 1;
  }
  const costs = cases.map(c => c.costUsd).filter((v): v is number => v !== undefined);
  const latencies = cases.map(c => c.latencyMs).filter((v): v is number => v !== undefined).sort((a, b) => a - b);
  const totalUsd = costs.reduce((sum, v) => sum + v, 0);
  const scoredCases = cases.filter(c => c.score !== undefined && c.label !== null && !NEUTRAL_LABELS.includes(c.label));
  return {
    total: cases.length,
    labelCounts,
    unlabelled: cases.filter(c => c.label === null).length,
    positive,
    scored: scoredCases.length,
    excludedUncertain: cases.filter(c => c.label !== null && NEUTRAL_LABELS.includes(c.label)).length,
    confusion: positive ? confusionAt(cases, threshold, positive) : undefined,
    sweep: positive ? thresholdSweep(cases, positive) : [],
    verdictByLabel,
    cost: { calls: costs.length, totalUsd, meanUsd: rate(totalUsd, costs.length) },
    latency: {
      calls: latencies.length,
      meanMs: rate(latencies.reduce((sum, v) => sum + v, 0), latencies.length),
      p50Ms: percentile(latencies, 0.5),
      p95Ms: percentile(latencies, 0.95),
    },
  };
}

export type ScopeProgress = {
  proposalsReviewed: number; proposalsTotal: number;
  labelledSufficient: number; positives: number; minSufficient: number; minPositive: number; done: boolean;
  /** Sufficient-input cases whose effective label is an unconfirmed agent first pass. */
  agentLabelled: number;
  /** Sufficient-input cases whose effective label is human. */
  humanConfirmed: number;
};

/** Progress toward the rubric minimum: >= 30 labelled sufficient-input cases, >= 5 positive. */
export function scopeProgress(cases: LabCase[], minSufficient: number, minPositive: number): ScopeProgress {
  const labelled = cases.filter(c => c.sufficient === true && c.label !== null);
  const positives = labelled.filter(c => c.label === c.positiveLabel).length;
  const proposed = cases.filter(c => c.proposal !== undefined);
  return {
    proposalsReviewed: proposed.filter(c => c.labelBy === "human").length, proposalsTotal: proposed.length,
    labelledSufficient: labelled.length, positives, minSufficient, minPositive,
    agentLabelled: labelled.filter(c => c.labelBy === "agent").length,
    humanConfirmed: labelled.filter(c => c.labelBy === "human").length,
    done: labelled.length >= minSufficient && positives >= minPositive,
  };
}

/** P(yes) bands used to stratify the queue, highest (scarcest positives) first. */
export const QUEUE_BANDS: ReadonlyArray<readonly [number, number]> = [[0.9, 1.01], [0.8, 0.9], [0.5, 0.8], [0, 0.5]];

/**
 * Labelling queue: unlabelled, sufficient-input cases with an outcome, taken round-robin
 * across P(yes) bands so the labelled set spans the range. Newest first within a band.
 */
export function buildQueue(cases: LabCase[], limit: number): LabCase[] {
  const pool = cases.filter(c => c.label === null && c.sufficient === true && c.score !== undefined);
  const bands = QUEUE_BANDS.map(([lo, hi]) => pool
    .filter(c => (c.score ?? 0) >= lo && (c.score ?? 0) < hi)
    .sort((a, b) => (b.timestamp ?? "").localeCompare(a.timestamp ?? "")));
  const queue: LabCase[] = [];
  for (let round = 0; queue.length < limit && bands.some(b => round < b.length); round++) {
    for (const band of bands) {
      const next = band[round];
      if (next && queue.length < limit) queue.push(next);
    }
  }
  return queue;
}

/**
 * Review-proposals queue: sufficient cases with a proposal that are unlabelled or only agent-labelled
 * (they still need confirmation), disputed first, then proposed overreach, then the rest; ties
 * broken by descending P(yes).
 */
export function buildProposalQueue(cases: LabCase[], limit: number): LabCase[] {
  const rank = (c: LabCase): number => (c.proposal?.agreement === "disputed" ? 0 : c.proposal?.label === "overreach" ? 1 : 2);
  return cases
    .filter(c => (c.label === null || c.labelBy === "agent") && c.sufficient === true && c.score !== undefined)
    .sort((a, b) => rank(a) - rank(b) || (b.score ?? 0) - (a.score ?? 0))
    .slice(0, limit);
}
