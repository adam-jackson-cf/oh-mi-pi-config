# Error analysis

## Objective

Turn measured errors into the next fix.

## Guidance

- Run this after a new scoring run on `dev`, and first when the user reports errors and a labeled
  `scores.csv` exists.
- When reviewing a claimed change between versions, first recompute each version's ranking on the
  cases that comparison used, and report it as the verdict on the claim.
  Then drop cases production would never ask before error analysis; report their count, and any
  ranking on them, separately as non-production evidence.
- When [threshold tuning](threshold-tuning.md) reports ranking `fails`, give the question, not each
  error, one cause: question wording, missing or clipped state, question overlap, or a policy gap;
  never a threshold, band or more cases of the same kind.
  Ranking `insufficient units` gets no question-level cause and needs cases from more units; still
  list each error below and give it a cause, including the fresh-context check.
- List each question's errors at its threshold in `thresholds.json`: the `dev` rows scoring at or
  above it with label 0, or below it with label 1; `--errors` uses only the script's own threshold.
  With no entry, ask the user once for the threshold or the file holding it; if they name none, use
  the best-metric threshold of the zero-minimum `--errors` run in
  [threshold tuning](threshold-tuning.md), and say which in the report.
  For an "any" flag, also list combined errors with the questions that triggered them.
- Join each error by `case_id` and `question_id` to `cases.jsonl`, `labels.jsonl`,
  `questions/<question-id>@<version-key>.json` and any logged candidates and answers.
  When the state or question text is missing, ask the user once for it; without it, record in
  `shortfalls` that cause analysis is blocked, and report the error list alone.
  Propose corrections to `unknown` labels for the arbiter in [labeling cases](labeling-cases.md) to
  decide case by case; append its decisions to `labels.jsonl` and the corrected labels to
  `scores.relabeled.csv`, never to the user's file.
- Give each error one cause, and apply its fix:
  - Label error: append a corrected row to `labels.jsonl` and note the rule; an agent's correction
    overrides a counting label only once the arbiter decides that case individually and blind.
  - Policy gap: agree the rule with the user, update the label guide, and relabel affected cases.
  - Missing or clipped state: add the evidence or a code-computed fact to the state.
  - Question wording: narrow or restate it, add boundary examples or a no-match, and change the
    version key.
  - Question overlap: the positive's reason meets another question's criterion; narrow one question
    or move the case, as [question design](question-design.md) describes.
  - Rule a parser could decide: move it into code or the deterministic router.
  - Model limit: check the jaggedness page, then split the question or escalate that case type.
  - Question shape: the question asks for a whole verdict, compares two items, searches a list,
    asks about a container rather than the evidence unit, or scores a correlate of the violation;
    redesign it with [design patterns](design-patterns.md) and label each link, before rewording or
    retiring it.
  - Code or service failure: fix the integration; it is not a model error.
- Before recording a model limit, show a fresh-context subagent, or the user, only the question,
  criteria and state: if its answer matches Jev and the written policy decides the case, it is a
  label error; if it matches the label and the state holds the evidence, a model limit; if the
  state lacks the evidence, missing state; otherwise a policy gap.
  When model calls are not allowed, mark model-limit causes `candidate` and list the fresh-context
  check as the next step.
- STOP when a cause is a policy gap or an undefined term, or a subject-path change alters which
  items are compared: ask the user for the definition before rewording or relabelling, end the
  turn, and never settle it inside a rewrite.
- When cases come in before/after pairs, check each pair's order: the positive must score above its
  partner; an inversion under every version points to construct or criteria, not wording.
- Before attributing a score shift between runs, re-score the same cases at least 3 times with an
  identical state hash, and report the spread per question with the state hash of each run; treat a
  shift inside that spread as noise.
- Between two scoring runs you compare, change only one of question text, state builder, or model or
  route.
  When more than one changed, score each change alone on the same cases, the cross of old and new,
  before attributing any difference to one of them.
  When an existing comparison already mixes changes, score that cross before making any further
  change to text, state or builder.
  In a prescribed step list, place the cross and the repeat runs before any step that changes
  question text, state builder, model, route or cases.
- Confirm a gain on held-out units from other repositories before claiming it: gains measured on
  one source's labels can vanish on another's.
- Fix the cause with the most errors first, then rerun: after label fixes, copy `scores.csv` to
  `scores.relabeled.csv` with corrected counting labels and re-tune on its `dev` rows; after a
  version key change, re-pin as the docs runbook describes, re-score `dev`, re-tune with the stored minimums, and run `test` once the version is final.
- Add the cases behind each fix to `cases.jsonl`, keeping their `source`, with stratum `regression`
  and split `dev`; every new version must pass them.
- After each pass, ask the user: "Accept the remaining errors, or fix the next cause?", and end the
  turn.
  Stop when they accept, recording the rest, and any recorded baseline not beaten, in `shortfalls`.
