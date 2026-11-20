---
name: "codify-omp-workflow-corrections"
description: "Use when the owner corrects an agent for a mistake,
  or asks to stop repeated mistakes in a repository."
hide: true
disable-model-invocation: true
---

# Task

## Classify and prevent mistakes

- **Purpose**: Turn recurring corrections into enforceable repository constraints.
- **When**: The owner identifies a mistake or asks to prevent repetition.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Runbook: [Class prevention](references/class-prevention.md)

## Maintain the rule table

- **Purpose**: Keep each correction traceable to an actual enforcer and its historical evidence.
- **When**: Adding, changing, or encountering a repeated correction rule.
- Runbook: [Rule table](references/rule-table.md)

## Report the result

- **Purpose**: Show what prevents each mistake class and why weaker enforcement was necessary.
- **When**: Completing a correction.
- Runbook: [Report the result](references/class-prevention.md#report-the-result)
