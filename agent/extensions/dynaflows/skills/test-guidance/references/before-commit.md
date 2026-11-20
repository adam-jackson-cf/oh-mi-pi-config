# Before committing

## Objective

Complete applicable verification without weakening assertions or hiding failures.

## Guidance

- Read Run events and Command notes in the repo test profile. Run its Commit command:
  static checks, all T1 and affected T2 boundary tests (seams and journeys).
  When committing under the caller's Git authority, let the commit checks run that command;
  never bypass them or a repo hook.
  This runbook grants no Git authority. If no Commit command is defined, report the gap, never invent a pass.
- Select additional checks by changed behaviour and blast radius, using `lsp references` and the repo test profile's affected-T2 selection.
  Follow [tiers.md](tiers.md) for event membership: CI runs all T1 and T2; nightly adds T3 on active branches.
  Do not default every local iteration to the whole suite; local green never replaces the CI merge gate.
- Retain comparable counts and timing evidence as [suite-budget.md](suite-budget.md) requires.
- In a test-only change, where no production file changed, leave every identity-bearing artefact
  listed in the profile byte-identical.
  A required check that is missing, skipped, timed out, failed for infrastructure reasons, or
  collected zero tests is a failure, not a pass; report it as blocked.
  Name commands, events and outcomes actually exercised.
- Only the repo's authorized fix command, recorded in Command notes, may write authorized check fixes.
  If none is designated, stay check-only. Do not use write mode to conceal failures.
- Drive caller input as [outcome-tests.md](outcome-tests.md) requires, never by private-state mutation or runtime patching.
- Never loosen expectations, thresholds or owned coverage rules to hide failures.
  Never add unapproved skip, xfail, focus/only or flaky/retry markers, rerun until green,
  or raise a timeout in a changed test to get a pass.
  Owner-approved measured timing-limit changes follow [suite-budget.md](suite-budget.md).
  Follow the repo's PR and review process.
