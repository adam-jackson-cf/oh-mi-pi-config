# Compose and integrate

## Objective

Combine questions into requests, read their answers correctly, and write integration code that fails
safely and stays versioned.

## Guidance

- Write the integration after [question design](question-design.md) has produced the questions.
- Ask independent questions over the same state together, including useful speculative questions.
  They run in parallel and cannot see one another's answers.
- State each speculative premise explicitly, and let code consume only the applicable answers.
- Make a second request only when an earlier answer is needed to fetch evidence, construct new
  state, or determine the next options.
- Measure actual request budgets, cost and end-to-end latency; extra questions still use tokens.
- Gate behavior on named, per-question thresholds set as [threshold tuning](threshold-tuning.md)
  describes.
- Read a probability as a confidence only after a [calibration check](calibration-check.md).
- Read Choice and Score confidence as distribution concentration, not overall workflow correctness
  or permission to act.
- Read a Noul near 0.5 as similar probability for yes and no, not medium intensity.
- Do not reject a harmless preference choice for low confidence; several acceptable alternatives can
  spread probability.
- Ignore uncertainty on unused branches.
- Keep policy explicit and raw judgments reusable.
- Use weighted scores for compensating preferences, and separate conditions for an "any serious
  violation" rule.
- Do not rerun inference to change a weight or a display filter when evidence and question meanings
  are unchanged.
- Treat typed output as a guaranteed interface, not guaranteed truth.
- Pin a versioned model ID rather than an alias such as `jev-latest`, which can change answers
  without a code change.
- Record the model each response reports, not the alias requested.
- Derive each question's version key as a hash of its instructions, criteria, state shape and model,
  not from the question ID.
- Treat a stored threshold or Platt mapping as stale when its version key changes,
  and re-score and re-tune before relying on it.
- Treat a failed or invalid Jev call as a failed evaluation or decision, never as a pass or a clean
  result.
- Keep API credentials server-side in web apps.
- Test representative cases and the resulting application behavior,
  and diagnose failures as [error analysis](error-analysis.md) describes.
