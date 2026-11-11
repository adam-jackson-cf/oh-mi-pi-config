---
name: "test-guidance"
description: "Use when creating, changing, moving or deleting tests, chasing flakes, controlling suite size or run time, or drawing claims from test evidence."
hide: true
disable-model-invocation: true
---

# Task

## 1. Start here

- **Purpose**: Assign tiers, run events and repo test profile values.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- A line `Enforced by <RULE-ID> (omp-guardrails <subcommand>)` marks a rule a guardrail check enforces.
  Run that check and follow its diagnostic; the contract lives in the [rule catalogue](../setup-repository-guardrails/assets/python-reference/omp_guardrails/data/guardrail-rules.json).
- Runbook: [Start here](references/tiers.md)

## 2. Choose what to test

- **Purpose**: Select realistic behaviour cases without duplicates.
- Runbook: [Choose what to test](references/case-selection.md)

## 3. Name and place

- **Purpose**: Name tests and place fixtures under their owner.
- Runbook: [Name and place](references/naming-and-layout.md)

## 4. Write an outcome test

- **Purpose**: Drive caller-visible outcomes through public entry points.
- Runbook: [Write an outcome test](references/outcome-tests.md)

## 5. Write a property test

- **Purpose**: Prove pure-core properties with generated inputs.
- Runbook: [Write a property test](references/property-tests.md)

## 6. Test determinism

- **Purpose**: Prove replay, seed and host parity.
- Runbook: [Test determinism](references/determinism.md)

## 7. Test behaviour and statistics

- **Purpose**: Support behavioural and measured claims.
- Runbook: [Test behaviour and statistics](references/behaviour-and-statistics.md)

## 8. Use the verification surface

- **Purpose**: Drive isolated journeys and maintain the feature map.
- Runbook: [Use the verification surface](references/verification-surface.md)

## 9. Chase a flaky test

- **Purpose**: Reproduce and remove intermittent failure causes.
- Runbook: [Chase a flaky test](references/flakes.md)

## 10. Review and prune

- **Purpose**: Retain meaningful coverage and remove brittle duplicates.
- Runbook: [Review and prune](references/suite-review.md)

## 11. Before committing

- **Purpose**: Complete required gates and report actual event results.
- Runbook: [Before committing](references/before-commit.md)

## 12. Control suite growth and time

- **Purpose**: Keep singly owned tests within approved local budgets.
- Runbook: [Control suite growth and time](references/suite-budget.md)
