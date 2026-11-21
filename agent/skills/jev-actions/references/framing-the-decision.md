# Framing the decision

## Objective

Agree what a decision does and costs, and discover its failure modes, before writing its questions
or collecting cases.

## Guidance

- Frame every judgment that will gate behavior or judge outputs before writing its questions;
  a verdict that code shows, stores, routes or acts on gates behavior.
  Reviewing or rewording existing questions needs no framing.
  Until the user answers, list draft questions in the STOP message only, one line each naming its
  primitive and condition; for a flag, one per draft trigger reason, for the user to add or remove.
  Skip framing when the task starts from an existing `scores.csv`; tuning then uses the defaults
  below for anything `thresholds.json` lacks.
  When `thresholds.json` already records some framing items, ask only for the rest.
- Frame with the user:
  - Action: what happens on a positive (log, review queue, confirm prompt, block, page), or for a
    Choice or Score the fallback below its threshold; who bears it, and whether it can be undone.
  - Baseline: what runs without Jev (rules, a person, an LLM judge, an existing route, or nothing).
  - Costs: of one false action and of one miss; for a Choice, following a wrong top option is a
    false action, and falling back from a right one is a miss.
  - Minimums: the precision and recall the decision must meet, and why.
  - Written policy: the rule in plain words, with exclusions and tie-breaks; it drives criteria,
    composition code and labels.
  - Terms: each word the written policy relies on that a labeler must interpret, such as "recent",
    "related" or "comparable", with a definition and one boundary pair, a case that counts and a
    near-miss that does not; put both in the criteria or label guide.
- STOP: ask in one message for the framing above, access to logs, who may review and read cases,
  the state fields judged, the Jev model ID if none is found, when `JEV_EVAL_DIR` is unset a
  capture path, and for a route the items in [route decisions](route-decisions.md); treat cases as
  possibly holding personal data unless the user says they hold none.
  End the turn, and resume when the user answers.
  Ask for an accuracy loss only when adding an escalation band, and for rulings only when labeling
  meets ambiguous cases.
- Record the answers with reasons in `thresholds.json` under a draft ID, the kebab-case ID each
  question will ship with, as [eval data schemas](eval-data-schemas.md) describes.
- For each item declined or left unanswered, assume a value, record it as assumed, and continue:
  - Action: log only, so a Choice or Score below its threshold drives nothing.
  - Baseline: whatever runs today, else nothing.
  - Costs: equal.
  - Minimums: precision 0.8 and recall 0.8, raising recall to 0.9 when the Costs answer says a miss
    costs more, or precision to 0.9 when it says a false action does; minimums given without a
    reason take reason `none given`; either keeps thresholds `provisional`.
  - Written policy: draft it from the user's goal.
  - Terms: the agent's draft definition and boundary pair, each recorded as assumed.
  - Reviewers: none, so only the agent reads cases, quoting none, and no labels are written.
  - Capture path: none, so no case is written.
  - State fields: a placeholder path such as `<summary-field>`, leaving Q7 `not checked`.
  - Other user-only report fields, such as label provenance: `unknown`.
- List every assumed value in the result, and ask the user to confirm or correct each one.
- Run in shadow mode until the user confirms them: the Jev call and its verdict sit behind a switch,
  off by default, so nothing acts on a verdict and the baseline keeps running; input logging follows
  only the capture rule in [eval data schemas](eval-data-schemas.md).
- Discover failure modes before writing questions:
  - Read 30 to 50 real inputs or traces, note what went wrong in free form, not as labels, and group
    the notes into named failure modes; each triggering reason of a flag is a mode.
    Describe personal data in notes, never quote it.
  - With fewer than 30 inputs, read all of them, and record the shortfall in `thresholds.json`.
  - With none, derive modes from the written policy.
  - Add the model weak spots that [question design](question-design.md) checks for.
- Repeat discovery when production errors stop matching the known failure modes, or once 30 shadow
  inputs arrive after a discovery pass that had none.
