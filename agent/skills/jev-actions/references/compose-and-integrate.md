# Compose and integrate

## Objective

Combine questions into requests, read their answers correctly, and write integration code that fails
safely and stays versioned.

## Guidance

- Use this runbook when the request goes beyond writing or reviewing questions.
- Ask independent questions over the same state in one request, including speculative ones; they
  run in parallel and cannot see one another's answers.
  State each speculative premise, and let code read only the applicable answers.
- Make a second request only when an earlier answer is needed to fetch evidence, build new state, or
  choose next options.
  Batching per-item questions in one request costs no accuracy, and two wording arms can share a
  request, which compares them on identical items.
- Measure precision and recall at the unit production blocks, after code combines answers: "any"
  over per-item answers multiplies false flags, and "all" over a Chain's links reduces them.
  Count items the candidate generator misses as recall misses.
- Test each candidate generator against fixtures holding known positives before scoring, and report
  its recall apart from Jev's ranking; a pattern that matches `temp` inside `templates` or a blanket
  test-path skip silently drops positives.
- omp's judge takes a Choice as `criteria: {label: rubric}`; an `options` map fails every call.
- Measure request budgets, cost and end-to-end latency.
- Read answers by what they measure:
  - Gate on each question's named `decision` from [threshold tuning](threshold-tuning.md), enforced
    from the moment it ships, so production acts on the decision rather than logging it: a single cutoff is yes at score >= `threshold`, else no, and a band
    sends its middle to its fallback judge; use the cost-rule default, under the user's recorded override, until a threshold is tuned.
  - Read a probability as a confidence only after a [calibration check](calibration-check.md).
  - Choice and Score confidence is distribution concentration, not correctness or permission to act.
    Below its threshold, code takes the Choice or Score fallback agreed at framing instead of the
    top option or grade.
  - A Noul near 0.5 means yes and no are similarly likely, not medium intensity.
  - Low confidence on a harmless, non-gating preference choice can mean several acceptable options.
  - Ignore uncertainty on unused branches; typed output guarantees the interface, not the truth.
- Keep policy explicit and raw judgments reusable: weights for compensating preferences, and no
  rerun when only a weight or filter changes.
- Record the model each response reports; a mismatch with the pinned model ID is a failed call.
- Scores count only from a route production can call: read the integration's route check, and mark
  scores from any other route as non-counting.
- Derive each question's version key as a hash of its instructions, criteria, state shape, state builder
  version and model.
  A stored threshold or Platt mapping is stale when its version key changes; re-score and re-tune.
  When production's key omits a component the skill keys on, record it in `shortfalls`; existing
  keys stay valid until the code adds it, and the next re-pin includes it.
- Treat a failed or invalid Jev call as a failed decision, never a pass.
- Before proposing a split, a retirement, a new question or a new decision shape, read the code that
  loads questions and decisions; list each hard-coded count, type or field check as a dependency of
  the fix, and record it in `shortfalls` when it blocks.
  When that code cannot express the prescribed decision shape, record the code change as a blocker
  in `shortfalls`.
- Source the API key from a secret store or the environment variable the SDK reads, keep it
  server-side, and never hardcode, commit, log or print it; verify it by presence, never by value.
- Test representative cases and the resulting application behavior, and diagnose failures with
  [error analysis](error-analysis.md).
