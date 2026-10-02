# Jev Decision Evaluation Rubric

Applies to any Jev process. The process profile (see [profile-template.md](profile-template.md))
supplies every process-specific input: label kind and vocabulary, input-sufficiency criteria,
reference-label procedure, error costs, thresholds and change bar. Score each case independently,
before looking at Jev's answer where practical, and record every score with a one-line evidence
citation (case ID plus quoted excerpt).

## R1. Input sufficiency (scores the state the process built, not Jev)

- **2 Sufficient**: the profile's R1 criteria hold; everything the judgment needs is present and
  unclipped.
- **1 Partial**: needed evidence is present but clipped, stale or partly omitted.
- **0 Missing**: the evidence the question depends on is absent. The only correct answer is the
  question's no-match or abstain outcome; if the question has none, that is a question defect.

## R2. Reference label

Decide the label from the recorded state alone, then separately from the wider evidence (session
transcript, file, page) if you inspected it. If the two differ, record both; the difference is a
state or template defect, not a Jev error. Prior human labels override yours; report disagreements
rather than replacing either.

- **Reference labels** (the profile's label kind is `reference`): label the true class in the
  question's own vocabulary. Abstain only under the profile's abstention rule; close calls with
  the evidence visible must be decided.
- **Verdict grades** (label kind `verdict-grade`): grade the process verdict as `correct`,
  `false_positive`, `false_negative`, or the profile's other grades, using the profile's
  definition of the positive outcome. Grade whether the rule's condition held, not whether the
  user wanted the outcome: a confirm the user approved is `correct` when the condition held.
  Frequent approvals of one rule mean its action tier is too strict for its condition (move it
  towards a softer tier); they are not false positives.

## R3. Decision correctness

Compare the process outcome to the state-only R2 label:

- Agrees: **correct**.
- Positive outcome, negative label: **false positive**. Negative outcome, positive label:
  **false negative**. The profile states which is costlier.
- Abstained with R1 = 2 and a decidable label: **unwarranted abstention**.
- Decided where the label is abstain: **overconfident guess**.
- Error, timeout, no outcome or unexpected model: **unresolved** (count separately, never as a
  negative).

Attribute every non-correct case to exactly one cause, because each has a different fix:

| Cause | Evidence | Fix owner |
| --- | --- | --- |
| Missing evidence | R1 < 2 | State builder or template |
| Question defect | The question or its options cannot express the right answer | Question design |
| Jev error | R1 = 2, well-formed question, wrong answer | Threshold or question |
| Rule or code error | `stage` is `deterministic`, or verdict contradicts answers | Rule or code |
| Policy or label defect | No rule decides it, or near-twin cases carry opposite labels | Policy |

## R4. Confidence quality

The summarizer's headline score is the first answer's noul, otherwise its P(yes); it is absent when
the first answer has neither. Judge every other question from its own probabilities, read as
[jev-design.md](jev-design.md) "Read probabilities" describes.

- Correct with the winning probability ≥ 0.8: well calibrated. Correct below 0.6: weak; note it.
- Any positive probability ≥ 0.5 on a negative label: calibration concern; list it.
- Report nouls between 0.4 and 0.6 as uncertainty, never as partial positives; note when routing
  that band elsewhere would have avoided an error.
- Every case at or above the profile's action threshold must be confirmed against the wider
  evidence before it counts toward a recommendation.

## R5. Operational health (per evaluation window)

- Coverage: cases with an outcome over cases.
- Model pinning: every resolved model equals the expected version.
- Unresolved rate and its causes; failures logged before a case existed, when the profile has them.
- Input-sufficiency rate: share of R1 = 2. Below 80% means the state, not Jev, is the dominant
  limitation; report that before judging accuracy.
- Cost and latency against the profile's budget, when it has one.

## Aggregate verdict

Report counts per cell, then rates with denominators (never percentages alone):

- Accuracy on R1 = 2 cases; false-positive and false-negative counts, each with case IDs.
- Error causes per the R3 table.
- Abstention appropriateness: share of abstentions where R1 < 2.
- Sample size: fewer than 30 labelled sufficient-input cases, or fewer than 5 of the minority
  label, is too few to choose a threshold. Get more by replaying and labelling history now
  ([development.md](development.md)) rather than waiting for live traffic. A threshold is chosen on
  labelled data and frozen; never tune it on the set used to evaluate it.
- Value against the profile's baseline and the resulting recommendation, per
  [development.md](development.md) "Decide". A shortfall against a target is iteration input.

Comparisons across process versions or models are valid only when both sides were scored with this
rubric and the same profile. Report unversioned legacy records separately.
