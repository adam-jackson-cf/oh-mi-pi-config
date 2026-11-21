# Building eval data

## Objective

Collect and split the labeled cases that every threshold, calibration check and model comparison
depends on.

## Guidance

- Build eval data, once [framing the decision](framing-the-decision.md) is answered, before a
  threshold is marked `ready` or gates an action that cannot be undone, before any confidence band
  or model comparison drives behavior, when a gating judgment has no cases, and first for reported
  errors with no labeled `scores.csv`.
  When the task starts from a `scores.csv` holding `label` and `score`, use only the dev/test rules
  here; without `split`, every row is `dev`.
- Store every case, label and score as [eval data schemas](eval-data-schemas.md) describes.
- Take cases in this priority order:
  1. Replayed logs, traces, tickets, transcripts or commits, frozen: evaluate stored outputs, never
     regenerated ones; label a route as [route decisions](route-decisions.md) describes.
  2. Already-decided cases, such as escalations, review queues and support outcomes.
  3. Synthetic cases for strata real data misses, by someone other than the question's author,
     kept a minority and reported separately.
  4. Adversarial cases: elaborate wrong answers, judge-instructing inputs, and near-duplicates with
     opposite labels.
- Build every case's `state` with the production state builder and asking condition, the code that
  decides whether production asks the question and what state it sends, and record the builder's
  version in the case's `builder`.
  A case production would never ask is out of scope; leave it out of `cases.jsonl`.
  Re-run replayed raw artifacts, such as whole files, records or transcripts, through the builder;
  never store them as `state` directly.
- When nothing can be replayed, shadow-log each input to `cases.jsonl` as `source` `replay`, stratum
  `unassigned`, with 30% of units hashed stably to `test`; label after 14 days, extending in 14-day
  steps until about 30 of each label exist.
  With no cases yet, record the provisional threshold, write the question versions and label
  guides, and end the task once logging is in place.
- Stratify by source or segment and by failure mode, giving every stratum both labels, ideally 10
  or more of each; grow a short stratum before rewording questions, unless
  [threshold tuning](threshold-tuning.md) reports failed ranking, which wording, state or policy fixes.
- Over-sample cases near the decision boundary from a pilot run, and rare positives.
- Split cases 70% `dev` and 30% `test` by an independent unit, such as session, user, repository or
  conversation, before iterating on wording, state or composition.
- Before asking for labelling spend, run
  `python3 -B scripts/check_labels.py --cases </abs/cases.jsonl> --labels </abs/labels.jsonl> --proposals`
  over agent pre-labels; while it exits 1, add cases from more units instead of labelling.
- Tune and inspect errors on `dev` only, and run `test` once per shipped question version.
  Never add to `test` mid-iteration or carve it from cases already scored; once its results are
  seen, build the next one from a newer replay window.
