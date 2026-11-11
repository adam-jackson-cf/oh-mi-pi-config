# Before committing

## Objective

Complete applicable verification without weakening assertions or hiding failures.

## Guidance

- Read Run events and Command notes in the repo test profile. Run its Commit command:
  static checks, all T1 and affected T2 boundary tests (seams and journeys).
  When committing under the caller's Git authority, let the pre-commit hook run that command; never bypass it.
  This runbook grants no Git authority. If no Commit command is defined, report the gap, never invent a pass.
- Select additional checks by changed behaviour and blast radius, using `lsp references` and the repo test profile's affected-T2 selection.
  Follow [tiers.md](tiers.md) for event membership: CI runs all T1 and T2; nightly adds T3 on active branches.
  Do not default every local iteration to the whole suite; local green never replaces the CI merge gate.
- Retain comparable counts and timing evidence as [suite-budget.md](suite-budget.md) requires.
  Enforced by TEST.budget.per-test-time and TEST.budget.fast-tier-time (omp-guardrails budget-check).
- Enforced by TEST.identity-artefacts (omp-guardrails diff-check).
- Enforced by TEST.failure-propagation (omp-guardrails report-check, collection-check).
  Report a blocked required check (or guardrail coverage-ledger rule) as blocked, not green.
  Name commands, events and outcomes actually exercised.
- Only the repo's authorized fix command, recorded in Command notes, may write authorized check fixes.
  If none is designated, stay check-only. Do not use write mode to conceal failures.
- Drive caller input through the public entry point and the same production inputs/adapters callers use,
  never by private-state mutation or runtime patching.
  Self-mocking is enforced by TEST.no-self-mocking (omp-guardrails adapter).
- Never loosen expectations, thresholds or owned coverage rules to hide failures.
  Enforced by TEST.no-evasion (omp-guardrails adapter, report-check, diff-check).
  Owner-approved measured timing-limit changes follow [suite-budget.md](suite-budget.md).
  Follow the repo's PR and review process.
