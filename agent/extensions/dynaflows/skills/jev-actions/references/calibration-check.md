# Calibration check

## Objective

Decide whether a probability can be read as a confidence, and whether to apply a calibration
mapping.

## Guidance

- Check calibration when code reads a probability itself:
  confidence bands, an escalation band with two cut-offs, weighted composite scores, or displayed
  likelihoods.
- Use only [threshold tuning](threshold-tuning.md) for a single threshold that flags, blocks or
  escalates.
- Treat a probability as calibrated when, for example, roughly 80% of cases scored near 0.80 turn
  out true.
- Check on the user's data even when published tests found the model well calibrated; calibration on
  one dataset says little about another.
- Run the bundled script from the skill's directory, with the same minimums and options as tuning:
  `python3 -B scripts/tune_threshold.py <path/to/scores.csv> --calibration --json`,
  plus `--min-precision <P> --min-recall <R>`.
  It fits Platt scaling on each training fold, and reports raw and held-out Brier scores, raw and
  Platt reliability tables,
  the mapping fitted on all `dev` cases, and raw and mapped Brier scores on `test` when a split
  exists.
  Exit codes are as in [threshold tuning](threshold-tuning.md).
- By default keep the raw probabilities; a recalibration can make a well-calibrated model worse.
- When Platt scaling improves the held-out Brier score, apply the Platt mapping,
  `sigmoid(a * logit(score) + b)`, in code,
  and store it with the version key it was fitted on.
- Confirm the kept mapping once on `test`: its Brier score must not be worse than raw there.
  If it is worse, keep raw, record score space `raw`, and report the mapping as rejected.
- Thresholds and escalation-band cut-offs from tuning apply to raw scores, and by default code gates
  on the raw score.
  Retune on mapped scores only when code gates on the mapped probability; displaying it alone keeps
  score space `raw`.
  To retune:
  write a mapped `scores.csv`, rerun the script, and record score space `platt` in
  `thresholds.json`.
- Show probabilities as confidence only when every held-out reliability bin of the kept version
  has an observed rate within 0.10, inclusive, of its mean predicted value.
  Otherwise keep them internal, and tell the user they are not fit to show.
- For plots, isotonic regression or several models at once, apply the same rules by hand:
  stratified five-fold cross-validation with the same splits for every model, a mapping fitted on
  each training split
  and applied to the held-out cases, a fit on log-odds after clipping probabilities away from 0 and
  1,
  predicted against observed rates before and after, Brier scores, where lower is better,
  saved scores only, and a note of which models improved or got worse.
- Bin reliability plots at about 20 cases per bin, and use isotonic regression only with larger
  label sets.
