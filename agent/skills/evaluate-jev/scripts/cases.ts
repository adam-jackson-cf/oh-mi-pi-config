// Portable evaluation core for Jev decision cases (see references/case-format.md).
// Standard library only; erasable TypeScript so both `bun` and `node` (>= 23.6) run it.

export type LabelBy = "human" | "agent";

export type CaseAnswer = {
  id?: string;
  type?: "noul" | "choice" | "score";
  noul?: number;
  choice?: string;
  probabilities?: Record<string, number>;
  score?: number;
  confidence?: number;
};

/** The fields the shared metrics read; a full case (case-format.md) is a superset. */
export type MetricCase = {
  timestamp?: string;
  version?: string;
  verdict: string;
  score?: number;
  label: string | null;
  labelBy?: LabelBy | null;
  positiveLabel?: string;
  costUsd?: number;
  latencyMs?: number;
};

export type EffectiveLabel = { label: string; by: LabelBy; note?: string };

/** Fold label rows in file order: the latest human label wins, else the latest agent label. */
export function foldLabel(current: EffectiveLabel | undefined, next: EffectiveLabel): EffectiveLabel {
  return current?.by === "human" && next.by === "agent" ? current : next;
}

/** Headline score: the first answer's noul, else its P(yes). Undefined when neither exists. */
export function headlineScore(answers: CaseAnswer[]): number | undefined {
  const first = answers[0];
  if (first?.type === "noul" && first.noul !== undefined) return first.noul;
  return first?.probabilities?.yes;
}

export const UNVERSIONED = "unversioned";
export const CURRENT_VERSION = "current";

export type Versions = { current: string | null; versions: string[] };

/** Current = version of the newest case by timestamp, ignoring unversioned; never hard-coded. Current is listed first. */
export function versionsOf(cases: MetricCase[]): Versions {
  let newest: MetricCase | undefined;
  for (const c of cases) {
    if (c.version === undefined || c.version === UNVERSIONED) continue;
    if (!newest || (c.timestamp ?? "") > (newest.timestamp ?? "")) newest = c;
  }
  const current = newest?.version ?? null;
  const others = [...new Set(cases.map(c => c.version ?? UNVERSIONED))].filter(v => v !== current).sort().reverse();
  return { current, versions: current === null ? others : [current, ...others] };
}

/** `requested` is "current" (or empty) for the derived current version, else an exact version. No current version leaves cases unfiltered. */
export function casesForVersion<T extends MetricCase>(cases: T[], requested: string): T[] {
  const target = requested === "" || requested === CURRENT_VERSION ? versionsOf(cases).current : requested;
  return target === null ? cases : cases.filter(c => (c.version ?? UNVERSIONED) === target);
}

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
export function confusionAt(cases: MetricCase[], threshold: number, positive: string): Confusion {
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
export function thresholdSweep(cases: MetricCase[], positive: string): SweepRow[] {
  return Array.from({ length: 10 }, (_, i) => {
    const threshold = (50 + i * 5) / 100;
    return { threshold, ...confusionAt(cases, threshold, positive) };
  });
}

function percentile(sorted: number[], q: number): number | null {
  if (sorted.length === 0) return null;
  return sorted[Math.min(sorted.length - 1, Math.ceil(q * sorted.length) - 1)] ?? null;
}

export function computeMetrics(cases: MetricCase[], threshold: number): SourceMetrics {
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
