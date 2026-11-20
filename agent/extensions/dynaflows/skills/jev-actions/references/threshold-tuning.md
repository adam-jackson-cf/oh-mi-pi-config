# Threshold tuning

## Objective

Set each decision threshold from saved scores so that it meets the agreed minimums on held-out data.

## Guidance

- Tune whenever a Noul probability, or a Choice or Score confidence, gates behavior and each label
  has about 30 cases.
  Below that, set a provisional threshold as [building eval data](building-eval-data.md) describes.
- Never ship 0.5 or a cookbook number as a tuned threshold.
  A Noul that scores every true case 0.40 and every false case 0.10 is perfect at 0.30 and catches
  nothing at 0.5.
- Treat cookbook thresholds, the docs' illustrative floors (0.5 to 0.6) and demo results as starting
  points to evaluate,
  not as rules or permanent model limitations.
- Use the minimum precision (flagged cases that are real) and recall (real cases flagged) agreed at
  the STOP
  in [building eval data](building-eval-data.md), with the reason recorded.
- Favor precision for automated actions and alerts, and recall for screening and safety checks.
- Choose the optimized metric deliberately: F1 weighs precision and recall equally;
  F-beta with beta below 1 favors precision, and above 1 favors recall.
  Default to `--beta 2` when a miss costs more, `--beta 0.5` when a false action costs more, and F1
  otherwise.
- Score each question once over the labeled cases, save the raw probabilities in `scores.csv`,
  and tune on the saved scores without new model calls.
- Compare models or question versions on the same cases.
- When `scores.csv` already exists, check it against the schema in
  [building eval data](building-eval-data.md).
  If `model` or `question_version` is missing, add them from the pinned model and the derived
  version key, then continue.
- STOP when `thresholds.json` holds no minimums, agreed or assumed, or lacks report fields only the
  user can supply,
  such as provenance; fields the agent can measure or derive, such as model, version key, cost or
  latency, are not missing.
  Skip this STOP for a provisional threshold under about 30 cases per label.
  When `scores.csv` already exists, ask only this STOP, not the framing questions in building eval
  data:
  ask in one message for the minimum precision and recall with the reason,
  and `"Which of these fields can you supply: <list>?"` for the missing fields.
  End the turn, and resume when the user answers.
  If the user declines minimums, assume precision 0.8 and recall 0.8,
  raising recall to 0.9 when a miss is known to cost more, or precision to 0.9 when a false action
  does.
  Record them as assumed, and mark the threshold `provisional`.
  Write `unknown` for any field left unanswered.
- Run the bundled script from the skill's directory:
  `python3 -B scripts/tune_threshold.py <path/to/scores.csv> --min-precision <P> --min-recall <R>`.
  It exits 0 when it prints a report, 1 on invalid input or a failed `--self-test`, and 2 on a usage
  error.
  A group with fewer than 2 dev cases of a label gets an error line in the report, and the run still
  exits 0.
- Read how the script uses the optional `scores.csv` columns:
  - By default every row is `dev`; a `split` column makes it tune and cross-validate on `dev` and
    score `test` once at the shipped threshold.
  - It reports each `model` and `question_version` separately, on the same splits.
  - A `stratum` column adds precision and recall per stratum at the shipped threshold.
- Choose options:
  - By default the minimums apply to point estimates.
    Add `--conservative`, which requires the 95% lower bound of precision and recall to meet them,
    when the automated action is irreversible, or the user asks for minimums held at 95% confidence.
    A cost reason given for a minimum does not trigger it on its own.
  - With fewer than about 50 dev cases per label, add `--repeats 5 --conservative`, and report the
    result as noisy.
  - Add `--beta` for F-beta, `--errors` for the [error analysis](error-analysis.md) list, and
    `--json` for machine-readable output.
  - Keep `--seed` fixed so splits are reproducible.
- When the script cannot do the tuning, apply the same rules by hand:
  stratified five-fold cross-validation, the threshold that maximizes the metric among those meeting
  both minimums on training folds,
  held-out performance reported, the same splits for every model, a comparison with 0.5, and saved
  scores only.
- If no threshold meets the minimums, report that; never relax a minimum silently.
- Ship one threshold chosen on all `dev` cases with the same rule.
  Report the cross-validated numbers as its expected performance, and confirm it once on `test`.
- Treat the threshold as ready to ship only when fold thresholds agree within about 0.05,
  `test` meets the minimums, and every recorded stratum on `test` meets them; a failed fold or
  stratum keeps status `provisional`.
  Otherwise grow the label set or narrow the question first.
- Report, per decision:
  - Decision: question ID, version key and pinned model.
  - Label set: cases per label, provenance mix, dev and test sizes, and synthetic share.
  - Minimums and why, and the metric with its beta.
  - Default 0.5 on dev held-out, and the tuned threshold with held-out precision, recall, metric and
    fold range.
  - Test at the shipped threshold, per stratum where recorded, and any failed folds.
  - Cost per 100 cases, computed from the pinned model's pricing and the saved cases' state size,
    and latency where measured.
- Store the threshold in `thresholds.json` as named configuration beside its minimums, metric, label
  set and version key,
  never as a literal in code.
- Compare Jev with an alternative, such as a reasoning model or LLM judge,
  on tuned held-out performance against measured cost and latency, never on default-threshold
  results.
