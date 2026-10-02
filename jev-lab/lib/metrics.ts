import type { LabCase } from "./types";

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
