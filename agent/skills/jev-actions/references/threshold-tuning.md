# Threshold tuning

## Objective

Set each decision threshold from saved scores so that it meets the agreed minimums on held-out data.

## Guidance

- Tune whenever a Noul probability, or a Choice or Score confidence, gates behavior, with one
  `thresholds.json` entry per question ID; for an "any" flag over several Nouls, also compute its
  precision and recall by hand at the shipped thresholds.
- No threshold is universal: each question's cutoff is tuned for precision against its own target.
  Usefulness floor: a judgment is useful only when its held-out precision at the shipped threshold
  is 0.75 or higher, counted over at least 10 held-out flags. A precision target, or a shipped
  result, below 0.75 needs the user's recorded override: `override` in `thresholds.json` with who
  gave it, when and why. `scripts/tune_threshold.py` refuses a lower `--min-precision` without
  `--owner-override`.
- A judgment ships enforced when its precision is measured. One whose precision cannot be measured
  yet may ship in shadow mode (`mode: "shadow"` with a `shadow_exit` such as
  `{"labelled_flags": 10}`): it is asked and logged like an enforced one, never blocks, and its
  results are reviewed until the exit is met, then it is tuned and enforced. Retire a judgment only
  when it cannot do the job.
  An untuned judgment that ships enforced does so only under the user's recorded override, at the
  cost-rule default, with the missing measurement in `shortfalls`.
  The gates below decide only whether a threshold may be tuned or a version compared: when one
  fails, keep the cost-rule default and record the failed gate in `shortfalls`.
  No advisory or provisional state other than shadow mode holds enforcement back; the user's
  instruction decides conflicts.
- Precision target: 0.75 for every question by default; at 0.75, three of every four flags are real.
  Raise it only when the user, asked what a wrong flag costs, sets a higher target; record the
  target, that the user set it, and their reason. Never raise it from the kind of action alone.
- Starting cutoff: an untuned judgment runs at a cutoff of 0.8, the cost-rule default; tuning
  replaces it with the cutoff the labels support.
- Score every arm, deterministic ones included, with the owner exceptions production honours
  applied, and report how many false flags an exception would absorb.
- Write each decision as a single cutoff `{"threshold": t}`: yes at score >= t, else no.
  Write a band `{"no_at_most", "yes_at_least", "fallback"}` only when
  [escalation band](escalation-band.md) sets one; a band with no fallback judge is no band.
- Count labels per label with `scripts/check_labels.py`, as [labeling cases](labeling-cases.md) defines;
  tune only when it exits 0 without `--proposals`, which needs dev cases from at least 2 units per label.
  - Label floor: under about 30 counting labels of either label, do not tune; set the cost-rule
    default, and record in `shortfalls` how many more of each label are needed.
  - About 30 or more of each: tune.
- Treat 0.5, 0.8, cookbook thresholds, illustrative floors and demo results as starting points,
  never as tuned thresholds.
- Tune for precision: pass the question's precision target as `--min-precision`, and a
  `--min-recall` only when the user framed one; report recall at every cutoff either way.
  Targets come from [framing the decision](framing-the-decision.md); a missing one is 0.75.
- Set the metric to `--beta 0.5`, favouring precision, unless the user says a miss costs at least
  as much as a false action; then use F1.
- Run each question once over the labeled cases, save the raw scores in `scores.csv`, and tune on
  the saved scores without new model calls.
  Check an existing `scores.csv` against the [eval data schemas](eval-data-schemas.md).
  The script needs only `label` and `score`; record a missing `question_id`, `model` or
  `question_version` in `thresholds.json` as the user states it, else `unknown`, listed in
  `shortfalls`.
  Split a file holding several questions into `scores.<question-id>.csv` copies, never editing the
  user's file, asking once which rows are whose if that is unclear.
- Run the bundled script from the skill's directory with the absolute path of the scores file:
  `python3 -B scripts/tune_threshold.py </abs/path/scores.csv> --min-precision <P> --min-recall <R>`.
  It prints the full report and exits:
  - 0 when ranking passes in every group;
  - 1 on invalid input or a failed `--self-test`, and 2 on a usage error;
  - 3 when ranking `fails` in any group;
  - 4 when ranking has `insufficient units` in any group and fails in none.

  If it cannot run, report that tuning was not done.
- Check ranking before choosing any threshold or band, at any label count: the report gives each
  question's AUC on `dev`, the chance a labeled 1 scores above a labeled 0, with a 95% interval from
  resampling units, and a ranking status:
  - `insufficient units`: a label has dev cases from fewer than 2 units; add units.
  - `fails`: a measured interval includes 0.5, or AUC is below `--min-auc` (0.7); take the
    question to [error analysis](error-analysis.md).
  - `passes`: `decision` holds the tuned single threshold.

  Unless ranking passes, `decision` is null: set no tuned threshold or band, set the cost-rule
  default, and record the ranking status in `shortfalls`.
- Hold out by source: group folds by repository or source and leave one out at a time. When several
  arms or compositions are compared, choose the arm inside each training fold, or confirm the chosen
  one on a fresh set, since the best of several on the same held-out data overstates it; report a
  bootstrap interval for held-out precision.
- Optional `scores.csv` columns: `split` (default all `dev`); `unit`, kept in one fold, and an exit 1
  if one spans `dev` and `test`; `model` and `question_version`, each pair its own group, marked
  `comparable: false` unless cases, labels, splits and units match; `stratum` for per-stratum results.
- Under about 50 dev cases per label, add `--conservative --repeats 5` and report the result as
  noisy; add `--conservative` alone when the Action answer says the action cannot be undone.
- `--errors` lists errors for [error analysis](error-analysis.md); `--json` gives machine output.
- If no threshold meets the minimums, `decision` is null: keep the cost-rule default and record
  the unmet minimums in `shortfalls`; for information only, rerun with
  `--min-precision 0 --min-recall 0` and report the precision and recall at each cutoff, and never
  relax a minimum silently.
- Before the end states, review errors at the best cutoff with [error analysis](error-analysis.md):
  ranking near or below chance (AUC about 0.5 or less) is a question-shape problem no cutoff fixes.
- Ship one threshold chosen on all `dev` cases with the same rule, and report the cross-validated
  numbers as its expected performance.
- Score `test` once at the shipped threshold; when it misses the minimums, record why in
  `shortfalls` and grow the label set or narrow the question, keeping the threshold enforced.
  Report the fold range and per-stratum results without gating on them.
- End states: when held-out precision stays below 0.75 at every cutoff, a question cannot reach
  about 30 in-scope cases per label, or ranking still fails after the fix passes in
  [error analysis](error-analysis.md), including a decomposition from
  [question design](question-design.md), ask the user to choose, and end the turn:
  1. Replace the question with deterministic facts computed in code.
  2. Turn it into an agent instruction on a deterministic harness event, such as a tool-call hook.
  3. Retire the question.
  4. Keep enforcing it under the user's recorded override, with the measured precision in
     `shortfalls`.

  A question cannot reach about 30 cases only when the full history of every source the user names,
  such as repositories, yields fewer; ask the user once for more sources before declaring it.
- Report per decision: question ID, version key and model; cases per label, provenance mix, split
  sizes and synthetic share; precision target and who set it, minimums, reasons and metric; held-out
  precision against the 0.75 floor with its flag count, and any override; AUC, its interval and ranking status;
  held-out results at 0.5 and the tuned threshold with fold range; `test` per stratum; cost per 100
  cases and latency; fields `unknown`.
- Store the decision in `thresholds.json` as named configuration, never as a literal in code.
- Compare Jev models, question versions or alternatives only when the label check and ranking pass
  for each, on the same cases and splits, by tuned held-out performance against measured cost and
  latency, never at default thresholds, and with one change between compared runs, as
  [error analysis](error-analysis.md) requires.
