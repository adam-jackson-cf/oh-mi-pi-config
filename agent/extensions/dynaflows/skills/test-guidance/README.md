# test-guidance

## Overview

Analyse a repository's existing test suite and install the testing guidance its agents follow from then on.
The workflow maps where the repository already keeps testing rules, commands and helpers, compares its rules and tests
with a fixed testing approach from files alone, and lets the owner choose how much to adopt before anything runs.
It then runs the repository's own test commands in place, presents one implementation plan for sign-off,
applies the approved plan and verifies it in the repository.

### How it decides

- **The owner chooses the adoption.** `pointer-only` adds pointers and runbooks for missing rules only;
  `merge` puts missing rules into the agent instruction files that already hold rules on each topic; `full` makes the
  installed runbooks the authority. `merge` is recommended when an instruction file already states testing rules.
  Human documentation such as READMEs and docs folders is read and routed to, never edited.
- **Existing homes win.** Every object goes where an instruction file says such objects go, else where similar objects
  already live, and only then to the skill's default. Conflicts between the repository's rules and the approach are
  listed with both sides for the owner to decide.
- **A workflow index, not a manual.** The installed skill is a short index at `.agents/skills/testing-approach/SKILL.md`:
  tier map in the repository's own vocabulary, repository facts, rules for every task, and task routes to existing
  repository sections or to runbooks in its `references/` folder (writing tests, boundary tests, flaky tests,
  suite maintenance, property tests, golden files). Agents load only the runbook their task needs.
- **Runs only what it installs.** Each command the installed text states runs once in place. The full suite baseline runs
  only when the plan can change code, so the applied changes are compared against it.
- **Guidance, not enforcement.** The skill installs no hooks, CI jobs or checkers, and never changes what an existing gate runs.
- **Migration is opt-in.** Breadth `none` changes no existing test and lists migration candidates as follow-up tasks;
  `exemplars` migrates at most three representative tests.

Requirements: Git and the target's own test runner.

## When to use it

- A repository's tests mix styles and agents keep adding tests the team then rewrites.
- A repository already has testing rules and needs agents routed to them, with the gaps filled.
- A new repository needs a testing approach its agents follow from the first test.

## Example prompts

- "Use `test-guidance` to analyse this repository's tests and show me the adoption options before running anything."
- "Use `test-guidance` on this Python service with merge adoption and no test migrations."
- "Use `test-guidance` to refresh the testing guidance installed last quarter after our move to Vitest."

## References

- [Step 0: Map the testing homes](references/step-0-map-workflow.md)
- [Step 1: Compare rules statically](references/step-1-compare-workflow.md)
- [Step 2: Adoption choice](references/step-2-adoption-workflow.md)
- [Step 3: Baseline in place](references/step-3-baseline-workflow.md)
- [Step 4: Implementation plan](references/step-4-plan-workflow.md)
- [Step 5: Apply and verify](references/step-5-apply-workflow.md)
- [Suite signals](references/suite-signals.md), [placement](references/placement.md), [tiers](references/tiers.md)
