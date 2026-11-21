# Labeling cases

## Objective

Produce labels that measure exactly what each question asks, with their provenance recorded.

## Guidance

- Before labeling, write a one-page label guide per question version: its criteria word for word as
  the yes and no definitions, exclusions, tie-breaks and two worked examples per label; relabel
  affected cases when the wording changes.
- Let domain reviewers or observed outcomes decide labels.
- Label blind: labelers see the unit and its evidence, never the expected verdict, Jev's answer or
  the process verdict, and cite the policy rule that decided each label.
- Use two independent labelers where possible: separate people, or sessions with no shared context.
  Their agreement sets the label; adjudicate disagreements with wider evidence, and record why.
- Use model pre-labels only to order review, never to skip confident cases;
  LLM judges tend to repeat Jev's confident errors.
- When many labels cite one rule, check whether it was read too broadly, clarify it, and re-derive
  those labels together.
- Near-duplicates with opposite labels mean the policy does not decide the case: fix it with the
  user and relabel before blaming the model.
- Keep cases labelers cannot agree on out of tuning, as review-routing or question-splitting evidence.
- Record each label's provenance:
  - `human`: a person decided it, including by confirming or changing an agent proposal.
  - `outcome`: an observed result decided it, such as a support resolution or a reverted change.
  - `agent_reviewed`: two independent agent sessions, ideally from different model families,
    agreed from the frozen guide, or an adjudicating session that saw both recorded why it chose.
  - `agent`: any other model label, such as a single session or a pre-label.
  - `unknown`: a label found only in `scores.csv`; it counts, but keeps the threshold `provisional`.
- When the user reviews agent proposals, show each case with its evidence and both labelers' reasons,
  allow no bulk accept, and append each decision as a `human` row with the reviewer in `by`, its
  own `timestamp` and `confirmed` `individual`; record any bulk accept as `confirmed` `bulk`.
- Append labels to `labels.jsonl`; never edit `cases.jsonl`.
- A case's counting label is its latest `human` label, else its latest `outcome`, `agent_reviewed`
  or `unknown` label; cases with only `agent` labels or an unresolved disagreement have none.
  Label set sizes, tuning and `scores.csv` use only counting labels.
- Take counting labels from
  `python3 -B scripts/check_labels.py --cases </abs/cases.jsonl> --labels </abs/labels.jsonl> --json`:
  a `human` row lacking `by`, `timestamp` or `confirmed` `individual`, or sharing its reviewer and
  timestamp with another case, counts as no label; it also checks unit coverage.
