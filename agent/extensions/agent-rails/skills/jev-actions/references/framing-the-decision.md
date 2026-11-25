# Framing the decision

## Objective

Agree what a decision does and costs, and discover its failure modes, before writing its questions
or collecting cases.

## Guidance

- Frame every judgment that will gate behavior or judge outputs before writing its questions;
  a verdict that code shows, stores, routes or acts on gates behavior.
  Reviewing or rewording existing questions needs no framing beyond the Terms step.
  Until the user answers, list draft questions in the STOP message only, one line each naming its
  primitive and condition; for a flag, one per draft trigger reason, for the user to add or remove.
  Skip framing when the task starts from an existing `scores.csv`; tuning then uses the defaults
  below for anything `thresholds.json` lacks.
  In a review or a `scores.csv` start, run the Terms step on the existing written policy before the
  question rubric.
  When `thresholds.json` already records some framing items, ask only for the rest.
- Frame with the user:
  - Action: what happens on a positive (log, review queue, confirm prompt, block, page), or for a
    Choice or Score the fallback below its threshold; who bears it, and whether it can be undone.
  - Baseline: what runs without Jev (rules, a person, an LLM judge, an existing route, or nothing).
  - Costs: of one false action and of one miss; for a Choice, following a wrong top option is a
    false action, and falling back from a right one is a miss.
  - Minimums: the precision the decision must meet, 0.75 unless the user sets a higher target
    after hearing what a wrong flag costs, and never below 0.75 without the user's override, as
    [threshold tuning](threshold-tuning.md) describes; a recall minimum only when misses matter
    enough to set one; and why.
  - Written policy: the rule in plain words, with exclusions and tie-breaks; it drives criteria,
    composition code and labels.
  - Terms: each word the written policy relies on that a labeler must interpret, such as "recent",
    "related" or "comparable", with a definition and one boundary pair, a case that counts and a
    near-miss that does not; put both in the criteria or label guide.
- STOP: ask in one message for the framing above, access to logs, who may read cases,
  the state fields judged, the Jev model ID if none is found, when `JEV_EVAL_DIR` is unset a
  capture path, and for a route the items in [route decisions](route-decisions.md); treat cases as
  possibly holding personal data unless the user says they hold none.
  End the turn, and resume when the user answers.
  Ask for an accuracy loss only when adding an escalation band, and for policy rulings only when
  labeling or error analysis meets a policy gap or an undefined term.
- Record the answers with reasons in `thresholds.json` under a draft ID, the kebab-case ID each
  question will ship with, as [eval data schemas](eval-data-schemas.md) describes.
- For each item declined or left unanswered, assume a value, record it as assumed, and continue:
  - Action: log only, so a Choice or Score below its threshold drives nothing.
  - Baseline: whatever runs today, else nothing.
  - Costs: equal.
    Costs come only from the user: record an agent's case for a higher target as `assumed`,
    and keep 0.75 until the user answers.
  - Minimums: precision 0.75 and no recall minimum;
    minimums given without a reason take reason `none given`.
    A recorded minimum with reason `assumed` is replaced by these defaults with the Costs answer
    applied; keep a minimum the user gave.
  - Written policy: draft it from the user's goal.
  - Terms: the agent's draft definition and boundary pair, each recorded as assumed.
  - Case readers: none named, so only the agent and the `innovation-council` labellers read cases,
    and notes describe personal data, never quote it.
  - Capture path: none, so no case is written.
  - State fields: a placeholder path such as `<summary-field>`, leaving Q7 `not checked`.
  - Other user-only report fields, such as label provenance: `unknown`.
- List every assumed value in the result and in `shortfalls`, and ask the user to confirm or correct
  each one; the judgment runs in its mode from the moment it ships, whether or not they have answered.
  Input logging follows only the capture rule in [eval data schemas](eval-data-schemas.md).
- Before writing questions, check the judgment can be measured and is a judgment at all:
  - Base rate: estimate how often real positives occur per unit asked. At 1% prevalence, precision
    0.8 at recall 0.5 allows a false-flag rate of about 0.16% per unit; a question with no real
    positives in the available history cannot be tuned until data exists. Rarity is a data gap to
    close by mining full history and more sources, never a reason to retire.
  - Code-detectable sub-cases: split out every shape of positive that code can detect, such as a
    pattern match. Code owns a shape outright only when every match is a violation and the shape
    alone meets the precision target; a shape that is sometimes legitimate becomes a proposer rule
    whose matches go to Jev. Jev keeps only the cases that need meaning.
  - Unit of judgment: check where real positives occur before fixing the unit production asks
    about. Ask of every item a change adds or modifies by default; narrow to new items only when
    labelled positives show modified items carry none. A folder-name judgment asked only of new
    folders misses violations that arrive as new files in existing folders.
- Discover failure modes before writing questions:
  - Read 30 to 50 real inputs or traces, note what went wrong in free form, not as labels, and group
    the notes into named failure modes; each triggering reason of a flag is a mode.
    Describe personal data in notes, never quote it.
  - With fewer than 30 inputs, read all of them, and record the shortfall in `thresholds.json`.
  - With none, derive modes from the written policy.
  - Add the model weak spots that [question design](question-design.md) checks for.
- Repeat discovery when production errors stop matching the known failure modes, or once 30 logged
  inputs arrive after a discovery pass that had none.
