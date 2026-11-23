# Building eval data

## Objective

Collect and split the labeled cases that every threshold, calibration check and model comparison
depends on.

## Guidance

- Build eval data, once [framing the decision](framing-the-decision.md) is answered, before a
  threshold is tuned or gates an action that cannot be undone, before any confidence band
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
- When real positives are rare in the user's own history, mine external data labelled by outcome,
  such as review comments a later fix confirmed, refactoring histories of renames, moves and merges,
  and expert architecture ground truths, and rebuild each case with the production builder at the
  commit before the fix.
  An outcome shows that something changed, not that the judged violation existed: label mined
  cases like any other, and use provenance `outcome` only when the outcome itself states the
  judgment.
- Draw candidates from every repository or source the judgment will govern, at several points in
  its history, by running the candidate generator over each snapshot and deduplicating items; leave
  out content the written policy does not govern, such as vendored bundles or fixture corpora, and
  record each exclusion and why.
- Settle policy rulings in the label guide before mining: a ruling on what counts can move
  prevalence and every label.
- Label a random sample of items the generator does not propose as well: precision on proposed
  items says nothing about violations the generator never proposes.
- Build negatives at natural prevalence by running the candidate generator over the whole
  pre-fix tree, never by hand-picking, and add hard negatives from the same candidate pool where
  the property is plausible; report precision on both, since a clean census can hide false flags.
- Synthetic or mutated cases serve regression only, at most 20% of `dev` and never `test` or
  threshold data; discard a set when a names-only baseline or a mutated-versus-sham discriminator
  scores AUC 0.6 or above.
  Have a separate agent author them from real code: list the directions the written policy can go,
  each rule both ways, about 5 cases per direction with hard cases in each (positives that look
  clean, negatives that look like violations), with no wording that reveals the direction.
  The council labels them blind to the intended label; a disagreement shows an unclear guide.
  Report each design's result per direction.
- Give samples stratified by score population weights before estimating recall from them.
- Before spending labels, run a signal check: score outcome-positive candidates, their near-misses
  and natural-prevalence units, and compare. A flat or inverted ranking shows a design that cannot
  work; a good ranking with low probabilities shows where the cutoff will sit.
- Check what the production builder extracts per language and where its caps cut: a builder that
  parses one language sends others as bare paths, and a tree cap in sorted order can drop the
  changed files' evidence in large repositories.
- Key each candidate item by question as well as path; one file can be a candidate for two
  questions.
- Build every case's `state` with the production state builder and asking condition, the code that
  decides whether production asks the question and what state it sends, and record the builder's
  version in the case's `builder`.
  A case production would never ask is out of scope; leave it out of `cases.jsonl`.
  Re-run replayed raw artifacts, such as whole files, records or transcripts, through the builder;
  never store them as `state` directly.
- When production asks a case several questions, keep the case even when it was collected for one
  question or stratum, and label it on every question production asks it; a question with no label
  on a case is outside that case's eval, not a negative.
- When nothing can be replayed, log each input production asks to `cases.jsonl` as `source` `replay`, stratum
  `unassigned`, with 30% of units hashed stably to `test`; label after 14 days, extending in 14-day
  steps until about 30 of each label exist.
  With no cases yet, ship the judgment enforced at the cost-rule default from
  [threshold tuning](threshold-tuning.md) only under the user's recorded override, write the
  question versions and label guides, and end the task once logging is in place.
- Stratify by source or segment and by failure mode, giving every stratum both labels, ideally 10
  or more of each; grow a short stratum before rewording questions, unless
  [threshold tuning](threshold-tuning.md) reports ranking `fails`, which wording, state or policy fixes.
- Over-sample cases near the decision boundary from a pilot run, and rare positives.
- Split cases 70% `dev` and 30% `test` by an independent unit before iterating on wording, state or
  composition.
  The unit is the coarsest grouping whose cases share an author, codebase or conversation, such as
  a session, user, repository or conversation; never the judged item.
  For history replays it is the repository, unless the user approves a finer unit such as a
  before/after pair; record the choice as `unit_rule` in `thresholds.json`.
  When reviewing existing data, check that `unit` is not the judged item; if it is, recompute it
  under `unit_rule` before running `check_labels.py`, which exits 1 when every case has its own unit.
- Before the council label pass, run
  `python3 -B scripts/check_labels.py --cases </abs/cases.jsonl> --labels </abs/labels.jsonl> --proposals`
  over agent pre-labels; while it exits 1, add cases from more units instead of labelling.
- Tune and inspect errors on `dev` only, and run `test` once per shipped question version.
  Never add to `test` mid-iteration or carve it from cases already scored; once its results are
  seen, build the next one from a newer replay window.
