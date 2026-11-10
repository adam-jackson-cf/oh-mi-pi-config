# Before committing

## Objective

Complete the applicable verification before committing without weakening assertions or hiding failures.

## Guidance

- Commit through the repo's pre-commit hook: it runs `checks.commit` from `.omp/gate.json`
  (static checks, every T1 test, and T2 seam tests only for affected code). Never bypass it.
  Treat the command array as the repo-owned invocation; do not substitute an easier command.
  If the manifest or `checks.commit` is absent, use the repo's documented pre-commit checks.
  If none is defined, report the missing check rather than inventing a pass.
- Select additional checks by changed behaviour and blast radius, using codegraph impact when
  available; include affected boundary cases and journeys when their wiring changed.
  CI runs `checks.ci` (full T1 and T2) on every push; do not default every local iteration to
  the whole suite.
- Use the repo runner's per-test duration report during targeted verification.
  Check applicable per-test and fast-tier budgets and retain the evidence for the PR.
- A skipped, missing or timed-out required tool is a failure, never a pass.
  A blocked check is not green; name what did not run and why.
  Use write-mode tools only when an authorized change calls for them, not to conceal a check result.
- Drive caller input through the same public input or client seam as production callers.
  Use the package's scripted or injected client for T1 wiring, not direct private-state mutation.
- Never fix a failure by weakening assertions or checks: no loosened expected value, lowered
  threshold, unowned coverage exclusion, skip, focused-only marker, retry or longer timeout.
- Report commands actually exercised and their outcomes; do not claim an unrun check passed.
  Include test-count and fast-tier duration deltas in the PR body as [suite-budget.md](suite-budget.md)
  requires, and follow the repo's PR and review process.
