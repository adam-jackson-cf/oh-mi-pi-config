# Building eval data

## Objective

Build the labeled cases, splits and files that every threshold, calibration check and model
comparison depends on.

## Guidance

- Frame the decision with the user before collecting anything:
  - Action: what happens on a positive (log, review queue, confirm prompt, block, page) and who
    bears it.
  - Baseline: what runs without the Jev step: deterministic rules, a person, an LLM judge, or
    nothing.
    Value is what Jev adds to it.
  - Costs: what one false action costs and what one miss costs; they set the minimums in
    [threshold tuning](threshold-tuning.md).
  - Written policy: the decision rule in plain words, with exclusions and tie-breaks.
    It drives the question criteria, the composition code and the labels.
- STOP: ask the user, together in one message, for the framing above, the consequences, the
  minimums,
  any acceptable accuracy loss for escalation, access to logs, who can review, and rulings on
  ambiguous cases.
  End the turn, and resume when the user answers.
- Record the agreed or assumed minimums, costs and accuracy loss, with their reasons,
  in `thresholds.json` in the eval data directory, where `scores.csv` will go.
- If the user declines or leaves items unanswered, assume a value for each, record it in
  `thresholds.json`,
  continue collecting cases, and keep any automated action in shadow mode until the user confirms.
  Without an agreed accuracy loss, follow the no-band rule in [error analysis](error-analysis.md).
- Do the replay, schema, pre-filtering, scripts and reports yourself.
- Discover failure modes before writing judges.
  Read 30 to 50 real inputs or traces, and write free-form notes on what went wrong, not labels.
  Group the notes into named failure modes, then write their questions as
  [question design](question-design.md) describes.
  For a flag with several triggering reasons, treat each reason as a mode.
  With fewer than 30 inputs, read all available, and record the shortfall in `thresholds.json`.
  With no inputs at all, derive the failure modes from the written policy, write the questions, run
  them in shadow mode,
  and repeat the discovery pass once 30 shadow inputs have arrived.
  Add the model weak spots that [question design](question-design.md) checks for.
- Repeat the discovery pass when production errors stop matching the known failure modes.
- Take cases from these sources, in priority order:
  1. Replay existing logs, traces, tickets, transcripts or commits now; a few hundred real units
     today beat a week of shadow traffic.
     Freeze them: evaluate stored outputs, and never regenerate them during an eval run.
  2. Already-decided cases: escalations, human review queues and support outcomes come with an
     observed label.
  3. Targeted synthetic cases for strata the real data does not cover, written by someone other than
     the question's author.
     They lack real context, so keep them a minority and report them separately.
  4. Adversarial cases: elaborate but wrong answers, inputs that try to instruct the judge, and
     near-duplicates with opposite labels.
- By default replay history; use shadow mode only when nothing can be replayed,
  time-boxed, with labels added as data arrives.
- Stratify by input source or segment and by failure mode, and record each case's stratum.
- Give every stratum both labels, ideally 10 or more of each.
- Over-sample cases near the decision boundary from a pilot run, and mark them so base-rate
  estimates can exclude them.
- Collect more rare positives rather than relying on accuracy over a set that is almost all
  negatives.
- Split cases into `dev` and `test` before any iteration on question wording, state or composition.
  Split by an independent unit, such as session, user, repository or conversation, not by row, so
  near-duplicates stay on one side.
- Tune thresholds and inspect errors on `dev` only, and score `test` once per shipped question
  version.
- Once test results have been seen, build a fresh test set from a newer replay window rather than
  reusing it.
- Never add cases to `test` during an iteration.
- Keep these files, redacting secrets before writing any of them:
  - `cases.jsonl`, one row per case: `case_id, unit, state, stratum, split, source`,
    where `source` is `replay`, `outcome`, `synthetic` or `adversarial`.
  - `labels.jsonl`, one appended row per label event:
    `case_id, label, by, provenance, rule, note, guide_version, timestamp`.
  - `questions/<id>@<version>.json`: each question version exactly as sent.
  - `scores.csv`, one row per case and question version:
    `case_id, label, score, model, question_version, split, stratum`,
    plus a `fallback` column for a fallback judge's verdicts when one is measured; the script
    ignores extra columns.
    `label` is 1 when the condition holds; for an eval judge, when the case contains the failure.
    Take the latest human label, else the latest `outcome` or `agent_reviewed` label,
    and omit cases with only `agent` labels or an unresolved disagreement.
    `question_version` holds the version key that [compose and integrate](compose-and-integrate.md)
    defines.
  - `thresholds.json`, beside `scores.csv` unless the project already has one, one entry per
    decision:
    threshold, minimums, metric, label set summary, version key, score space (`raw` or `platt`),
    status (`provisional` or `ready`), the cost rule that set a provisional threshold, and recorded
    assumptions.
    When an escalation band is set, also its low and high cut-offs, the fallback judge,
    the cascade accuracy and escalation rate on dev and test, and the agreed accuracy loss.
- Size the set by cases per label, counting only `human`, `outcome` and `agent_reviewed` labels:
  - Fewer than about 30: do not tune.
    Set a provisional threshold of 0.3 when a miss costs more than a false action,
    0.7 when a false action costs more, and otherwise 0.5, the lower of the docs' illustrative
    floors.
    Record it in `thresholds.json` with status `provisional` and the cost rule that set it.
  - About 30 to 50: tune, and mark the threshold `provisional`.
  - For any `provisional` threshold, leave a visible TODO naming the missing cases,
    and keep any automated action reversible or in shadow mode.
  - 50 to 100 or more: tune, and record status `ready` when [threshold tuning](threshold-tuning.md)
    reports it ready,
    otherwise `provisional`.
- When one stratum misses its minimums, grow that stratum before rewording questions.
