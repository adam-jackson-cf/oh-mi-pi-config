# Calibration check

## Objective

Decide whether a probability can be read as a confidence, and whether to apply a calibration
mapping.

## Guidance

- Check calibration when code reads a probability itself: confidence bands, an escalation band,
  weighted composite scores, or displayed likelihoods.
  A single threshold needs only [threshold tuning](threshold-tuning.md).
- Calibrated means, for example, about 80% of cases scored near 0.80 are true; check it on the
  user's data whatever published tests found.
- Run the bundled script from the skill's directory with tuning's minimums and options:
  `python3 -B scripts/tune_threshold.py </abs/path/scores.csv> --calibration --json --min-precision <P> --min-recall <R>`.
  It reports raw and held-out Brier scores, raw and Platt reliability tables, the Platt mapping fitted
  on all `dev` cases, and raw and mapped Brier on `test`, with tuning's exit codes.
  If it cannot run, report that calibration was not checked, and keep probabilities internal.
- Keep raw probabilities unless Platt scaling improves the held-out Brier score and is no worse on
  `test`; then apply `sigmoid(a * logit(score) + b)` in code, stored with the version key it was
  fitted on; otherwise record score space `raw` and report the mapping as rejected, since
  recalibration can make a well-calibrated model worse.
- Thresholds and band cut-offs apply to raw scores by default; only when code gates on the mapped
  probability, write a mapped `scores.csv`, retune, and record score space `platt`.
- Show probabilities as confidence only when, in the held-out reliability table for the value code
  shows, every bin of about 20 or more cases has an observed rate within 0.10 of its mean score;
  report the largest gap, and otherwise keep them internal and say so.
