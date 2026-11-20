# Eval data schemas

## Objective

Store eval files where they belong, in shapes every later step and the tuning script can read.

## Guidance

- Use this file whenever a step writes or checks `thresholds.json`, `cases.jsonl`, `labels.jsonl`,
  `questions/` or `scores.csv`.
- Keep `thresholds.json` and `scores.csv` in the eval data directory: the project's existing one,
  else `evals/` at the repository root.
  Add `cases.jsonl`, `labels.jsonl` and `questions/` there only once cases, labels or question
  versions are stored.
- When cases may hold personal data, write `cases.jsonl` and `labels.jsonl` instead to the
  directory named by `JEV_EVAL_DIR`, outside the repository and readable only by the reviewers the
  framing answer names.
  When it is unset and framing recorded no path, write no case, record in `shortfalls` that
  discovery and labeling wait on a capture path, and end the task.
- Redact secrets before writing any file.
- `thresholds.json` at the directory root, one entry per question ID, per draft ID from framing,
  or `unknown`: the framing answers, threshold, metric, label set summary, version key, score
  space (`raw`, or `platt` with its `a` and `b`), status (`provisional` or `ready`), the cost rule
  behind a provisional threshold, assumptions with reasons, and `shortfalls`, each missing input
  with what it blocks.
  With an escalation band, also its cut-offs, fallback judge, cascade accuracy and escalation rate
  on dev and test, and the agreed accuracy loss.
- `cases.jsonl`: `case_id, unit, state, stratum, split, source`,
  with `source` one of `replay`, `outcome`, `synthetic` or `adversarial`.
- `labels.jsonl`, appended per label event:
  `case_id, question_id, label, by, provenance, rule, note, timestamp`, with `label` 1 or 0, or
  for a Choice the correct option key, and `note` holding any adjudication reason.
- `questions/<question-id>@<version-key>.json`: each question version exactly as sent, beside its
  label guide `<question-id>@<version-key>.md`.
- `scores.csv`, one row per case and question version:
  `case_id, question_id, label, score, model, question_version, split, unit, stratum`, plus
  `fallback` when measured; the script ignores extra columns; `model` is the Jev model ID that
  produced `score`, never a routed model.
  - `score` is the raw value code compares with the threshold: a Noul probability, or a Choice or
    Score confidence.
  - `label` is 1 when the case should trigger the gated action, or for an eval judge has the
    failure, from the counting label in [labeling cases](labeling-cases.md).
    For a Choice, `label` is 1 when Jev's top option is the correct one, and `score` is its
    confidence; tune its `any_fits` Noul as its own question.
    [Route decisions](route-decisions.md) define the correct option of a route.
  - `question_version` is the version key from [compose and integrate](compose-and-integrate.md).
  - With personal data, `case_id` and `unit` are pseudonymous hashes.
