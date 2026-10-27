# Labeling cases

## Objective

Produce labels that measure exactly what each question asks, with their provenance recorded.

## Guidance

- Write a one-page label guide per question.
- Use the question's own criteria, word for word, as the guide's yes and no definitions.
- List the exclusions and tie-break rules, with two worked examples per label.
- Version the guide with the question's version key, and relabel the affected cases when the wording
  changes.
- Show labelers state that contains the evidence, never the expected verdict or Jev's answer.
- Let domain reviewers or observed outcomes decide labels; a human label always overrides an agent
  label.
- Use model pre-labels only to order review work, never to skip review of confident cases.
  LLM judges tend to repeat Jev's confident errors, so their agreement with Jev is weak evidence of
  correctness.
- Label blind: labelers see the unit, not Jev's answers or the process verdict, and cite the policy
  rule that decided each label.
- Use two independent labelers where possible: separate people, or separate sessions with no shared
  context.
- Set the label where they agree.
  Adjudicate disagreements with the wider evidence, and record why.
- When many labels cite one rule, check whether a labeler read that rule too broadly.
  Clarify the rule, and re-derive those labels together.
- When near-duplicates carry opposite labels, the policy does not decide that case.
  Fix the policy with the user, and relabel before blaming the model.
- Keep cases that labelers cannot agree on out of tuning.
  Use them for review routing, or as evidence that the question needs splitting.
- Record each label's provenance as `human`, `outcome`, `agent_reviewed` or `agent`:
  - `human`: a person decided the label, including by confirming or changing an agent proposal.
  - `outcome`: an observed result decided it, such as a support resolution or a reverted change.
  - `agent_reviewed`: an agent proposal made blind, from the frozen guide, by two independent
    sessions with no shared context, ideally from different model families. Their agreement sets
    it, or an adjudicating session that sees both answers but never Jev's records why it chose.
  - `agent`: any other model label, such as a single session, a pre-label, or one labeler's row.
- When the user reviews agent proposals, show every case with its evidence and both labelers'
  reasons, never Jev's answer, and allow no bulk accept.
  Append each decision as a `human` row with `by` naming the reviewer.
- Append labels to `labels.jsonl` rather than editing `cases.jsonl`; the latest human label for a
  case wins.
