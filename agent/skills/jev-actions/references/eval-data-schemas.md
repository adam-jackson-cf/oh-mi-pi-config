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
  or `unknown`: the framing answers, `decision`, metric, label set summary, version key,
  `unit_rule`, score space (`raw`, or `platt` with its `a` and `b`), the cost tier with its
  precision target and starting cutoff, held-out precision with its flag count, any `override`
  (`by`, `timestamp`, `reason`) that keeps a judgment below the 0.75 usefulness floor enforced,
  assumptions with reasons, and `shortfalls`, each missing input, failed gate or thin or assumed
  evidence with what it blocks.
  - `decision` is `{"threshold": t}`, yes at score >= t, else no; only with a fallback judge may it
    be a band `{"no_at_most", "yes_at_least", "fallback"}`.
  - With an escalation band, also record its fallback judge, cascade accuracy and escalation rate on
    dev and test, and the agreed accuracy loss.
- `cases.jsonl`: `case_id, unit, state, stratum, split, source, builder`,
  with `source` one of `replay`, `outcome`, `synthetic` or `adversarial`, and `builder` the
  version of the production state builder that made `state`.
- `labels.jsonl`, appended per label event:
  `case_id, question_id, question_version, label, by, provenance, rule, note, timestamp, confirmed,
  blind`, with `label` 1 or 0, for a Choice the correct option key, or `abstain` with the reason in
  `note`, which any labeller may give, counts as no label and is reported per question; `unsure` is
  not a label value.
  `provenance` is one of `arbiter`, `outcome`, `agent_reviewed`, `agent`, `unknown` or `owner`, a
  person's label, which never counts and which no labeller or arbiter sees.
  `note` holds any arbitration reason, `question_version` the version key the label was made
  under, `confirmed` on `arbiter` rows `individual` when the arbiter decided this case alone, else
  `bulk`, and `blind` true when the labeller saw no score and no earlier label for the case.
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
