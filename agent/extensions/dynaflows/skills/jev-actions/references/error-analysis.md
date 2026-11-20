# Error analysis

## Objective

Turn measured errors into the next fix, and add escalation only where measurement on labeled cases
supports it.

## Guidance

- After each scoring run on `dev`, list the false positives and negatives with `--errors` from
  [threshold tuning](threshold-tuning.md).
  When no threshold qualifies, rerun with `--min-precision 0 --min-recall 0 --errors` to list errors
  at the best-metric threshold.
  For each one, inspect its state, question, score, composition and observed outcome,
  plus the candidates and answers when the logs keep them.
- Give each error one cause, and apply its fix:
  - Label error: fix the label and note the rule, leaving the question unchanged.
  - Policy gap: agree the rule with the user, update the label guide, and relabel the affected
    cases.
  - Missing or clipped state: change the application to add the evidence or a code-computed fact.
  - Question wording: narrow or restate the question, add boundary examples or a no-match, and
    change the version key.
  - Rule a parser could decide: move it into code or the deterministic router.
  - Model limit: check the jaggedness page, then split the question or escalate that case type.
  - Code or service failure: fix the integration, and do not count it as a model error.
- Fix the largest cause first, then rerun.
- Before blaming the model, run a perception check on about 20 cases per question:
  two blind labelers answer Jev's exact question from the same state Jev saw.
  If Jev agrees with their consensus about as often as they agree with each other,
  attribute the remaining errors to policy, state, composition or labels.
- Add the cases that drove each fix to a regression set that every new version must pass.
- Stop iterating when the process adds value over the baseline and the user accepts what remains,
  and record the rest as a backlog.
- Accept confident answers and escalate uncertain ones to a reasoning model or a person
  only after measuring both stages on the same labeled cases.
- Set the escalation band by hand from saved results:
  run the fallback judge once on the same `dev` and `test` cases, asking it each Noul's exact
  question,
  and save its per-question verdict in the `fallback` column of that question's rows in
  `scores.csv`.
  For each candidate band, with cut-offs in steps of 0.05, accept Jev outside the band and the
  fallback inside it,
  and compute the cascade's accuracy and escalation rate against the labels.
  Choose the narrowest band whose 95% Wilson lower bound of accuracy is within the user's agreed
  loss of fallback-only accuracy,
  whose cascade meets the agreed minimum precision and recall on dev, and which contains the tuned
  threshold.
  If no band qualifies, set none, and report it.
  Validate the band per question on `test`, and check calibration first, as
  [calibration check](calibration-check.md) describes.
- With fewer than about 30 cases per label, or no agreed accuracy loss, set no band, and record a
  TODO naming what is missing.
- In a judge with one Noul per failure mode, set a band per Noul, and escalate only the questions
  inside their band.
- Expect escalation to save cost more than it raises accuracy: a fallback judge with the same blind
  spots cannot fix Jev's confident errors.
  Prefer a fallback with different error patterns, or a person.
- Never route by confidence on derivation checks or on factuality without reference evidence,
  which [question design](question-design.md) keeps out of Jev,
  nor on comparisons that [question design](question-design.md) requires testing first, until they
  are tested.
- In production, send cases inside the escalation band to the fallback judge measured for that band,
  a reasoning model or a person,
  and log the state, score, model and version key of every decision.
- Add reviewed cases to `dev`, and also label a random sample of confident decisions into `dev`;
  confident errors never reach the review queue.
- Re-tune when a set number of new labels has arrived, or on a schedule.
