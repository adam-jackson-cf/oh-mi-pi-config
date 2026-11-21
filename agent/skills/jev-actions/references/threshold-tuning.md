# Threshold tuning

## Objective

Set each decision threshold from saved scores so that it meets the agreed minimums on held-out data.

## Guidance

- Tune whenever a Noul probability, or a Choice or Score confidence, gates behavior, with one
  `thresholds.json` entry per question ID; for an "any" flag over several Nouls, also compute its
  precision and recall by hand at the shipped thresholds.
- Count labels per label with `scripts/check_labels.py`, as [labeling cases](labeling-cases.md) defines;
  tune only when it exits 0 without `--proposals`, which needs dev cases from at least 2 units per label.
  - Fewer than about 30 of either label: do not tune; set a provisional threshold of 0.3 when a miss
    costs more, 0.7 when a false action does, else 0.5, record that cost rule, and report how many
    more of each label are needed.
  - About 30 or more of each: tune; status is `ready` only when the ready rule below holds.
  - A `provisional` threshold may gate only shadow-mode or reversible actions; record the missing
    cases in `shortfalls`.
- Treat 0.5, cookbook thresholds, illustrative floors and demo results as starting points, never as
  tuned thresholds.
- Use the minimum precision (flagged cases that are real) and recall (real cases flagged) from
  [framing the decision](framing-the-decision.md), favoring precision for automated actions and
  recall for screening; its defaults fill anything `thresholds.json` lacks, recorded as assumed,
  listed for the user to confirm, and keeping the threshold `provisional`.
- Set the metric from the Costs answer only: `--beta 2` when a miss costs more, `--beta 0.5` when a
  false action costs more, otherwise F1.
- Run each question once over the labeled cases, save the raw scores in `scores.csv`, and tune on
  the saved scores without new model calls.
  Check an existing `scores.csv` against the [eval data schemas](eval-data-schemas.md).
  The script needs only `label` and `score`; record a missing `question_id`, `model` or
  `question_version` in `thresholds.json` as the user states it, else `unknown`, which keeps the
  threshold `provisional`.
  Split a file holding several questions into `scores.<question-id>.csv` copies, never editing the
  user's file, asking once which rows are whose if that is unclear.
- Run the bundled script from the skill's directory with the absolute path of the scores file:
  `python3 -B scripts/tune_threshold.py </abs/path/scores.csv> --min-precision <P> --min-recall <R>`.
  It exits 0 with a report, 1 on invalid input or a failed `--self-test`, and 2 on a usage error;
  if it cannot run, report that tuning was not done.
- Check ranking before choosing any threshold or band, at any label count: the report gives each
  question's AUC on `dev`, the chance a labeled 1 scores above a labeled 0, with a 95% interval from
  resampling units, and fails it when the interval includes 0.5 or AUC is below `--min-auc` (0.7).
  When ranking fails, do not tune or band: keep the threshold `provisional` in shadow mode, record
  the failed ranking in `shortfalls`, and take the question to [error analysis](error-analysis.md).
- Optional `scores.csv` columns: `split` (default all `dev`); `unit`, kept in one fold, and an exit 1
  if one spans `dev` and `test`; `model` and `question_version`, each pair its own group, marked
  `comparable: false` unless cases, labels, splits and units match; `stratum` for per-stratum results.
- Under about 50 dev cases per label, add `--conservative --repeats 5` and report the result as
  noisy; add `--conservative` alone when the Action answer says the action cannot be undone.
- `--errors` lists errors for [error analysis](error-analysis.md); `--json` gives machine output.
- If no threshold meets the minimums, record `threshold: null`, status `provisional` and the unmet
  minimums in `shortfalls`; for information only, rerun with `--min-precision 0 --min-recall 0` and
  report its best-metric threshold, gate nothing on it, and never relax a minimum silently.
- Ship one threshold chosen on all `dev` cases with the same rule, and report the cross-validated
  numbers as its expected performance.
- Ready rule: `test` meets the minimums; report the fold range and per-stratum results without
  gating on them. Otherwise stay `provisional`, record why in `shortfalls`, and grow the label set
  or narrow the question.
- Report per decision: question ID, version key and model; cases per label, provenance mix, split
  sizes and synthetic share; minimums, reasons and metric; AUC and its interval; held-out results at 0.5 and the tuned
  threshold with fold range; `test` per stratum; cost per 100 cases and latency; fields `unknown`.
- Store the threshold in `thresholds.json` as named configuration, never as a literal in code.
- Compare Jev models, question versions or alternatives on the same cases and splits, by tuned held-out
  performance against measured cost and latency, never at default thresholds, and with one change
  between compared runs, as [error analysis](error-analysis.md) requires.
