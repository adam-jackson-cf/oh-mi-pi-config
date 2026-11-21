# Error analysis

## Objective

Turn measured errors into the next fix.

## Guidance

- Run this after a new scoring run on `dev`, and first when the user reports errors and a labeled
  `scores.csv` exists.
- When [threshold tuning](threshold-tuning.md) reports failed ranking, give the question, not each
  error, one cause: question wording, missing or clipped state, or a policy gap; never a threshold,
  band or more cases of the same kind.
- List each question's errors at its threshold in `thresholds.json`: the `dev` rows scoring at or
  above it with label 0, or below it with label 1; `--errors` uses only the script's own threshold.
  With no entry, ask the user once for the threshold or the file holding it; if they name none, or
  the entry is `null`, use the best-metric threshold of the zero-minimum `--errors` run in
  [threshold tuning](threshold-tuning.md); record either as a separate `provisional` entry.
  For an "any" flag, also list combined errors with the questions that triggered them.
- Join each error by `case_id` and `question_id` to `cases.jsonl`, `labels.jsonl`,
  `questions/<question-id>@<version-key>.json` and any logged candidates and answers.
  When the state or question text is missing, ask the user once for it; without it, record in
  `shortfalls` that cause analysis is blocked, and report the error list alone.
  Propose corrections to `unknown` labels as a table, and write confirmed ones as `human` to
  `scores.relabeled.csv`, never to the user's file.
- Give each error one cause, and apply its fix:
  - Label error: append a corrected row to `labels.jsonl` and note the rule; an agent's correction
    overrides a human label only once a person confirms it.
  - Policy gap: agree the rule with the user, update the label guide, and relabel affected cases.
  - Missing or clipped state: add the evidence or a code-computed fact to the state.
  - Question wording: narrow or restate it, add boundary examples or a no-match, and change the
    version key.
  - Rule a parser could decide: move it into code or the deterministic router.
  - Model limit: check the jaggedness page, then split the question or escalate that case type.
  - Code or service failure: fix the integration; it is not a model error.
- Before recording a model limit, show a fresh-context subagent, or the user, only the question,
  criteria and state: if its answer matches Jev and the written policy decides the case, it is a
  label error; if it matches the label and the state holds the evidence, a model limit; if the
  state lacks the evidence, missing state; otherwise a policy gap.
- Between two scoring runs you compare, change only one of question text, state builder, or model or
  route.
  When more than one changed, score each change alone on the same cases, the cross of old and new,
  before attributing any difference to one of them.
- Fix the cause with the most errors first, then rerun: after label fixes, copy `scores.csv` to
  `scores.relabeled.csv` with corrected counting labels and re-tune on its `dev` rows, keeping the
  threshold `provisional`; after a version key change, re-pin as the docs runbook describes,
  re-score `dev`, re-tune with the stored minimums, and run `test` once the version is final.
- Add the cases behind each fix to `cases.jsonl`, keeping their `source`, with stratum `regression`
  and split `dev`; every new version must pass them.
- After each pass, ask the user: "Accept the remaining errors, or fix the next cause?", and end the
  turn.
  Stop when they accept, recording the rest, and any recorded baseline not beaten, in `shortfalls`.
