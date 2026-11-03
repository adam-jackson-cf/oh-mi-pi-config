# Bug fix playbook

## Objective

Repair the reproduced failure class and prove the regression test fails before the fix.

## Steps

1. Capture the reported behaviour, expected behaviour, and a reliable reproduction.
2. Read `skill://codegraph/references/readiness-and-bootstrap.md` before repository discovery.
3. Read `skill://codegraph/references/query-selection.md`; trace the failure to its owning code.
4. Read `skill://git-tool`, then its branch-and-integrate runbook; allocate one writer branch.
5. Read `skill://coding-guidance/references/implementation-integrity.md` before the first edit.
6. Read `skill://coding-guidance/references/regression-coverage.md`; identify the failure class
   and delegate a representative regression test to `task`; observe it fail before the fix.
7. Read `skill://test-guidance/references/flakes.md` if reproduction is intermittent; establish
   controlled evidence instead of retries or test suppression.
8. Read `skill://test-guidance/references/suite-budget.md`; justify each added regression case.
9. Delegate every code change to `task`; repair the root cause and prove the regression passes.
10. Read `skill://coding-guidance/references/verification.md`; run the repository fast gate from
    `.omp/gate.json` after the final edit and record the result before handoff.
11. Read `skill://git-tool/references/review-loop.md`; obtain independent-session,
    cross-family review per the owner rules and resolve findings through `task`.
12. Read `skill://git-tool/references/branch-and-integrate.md`; integrate the verified
    unit as one PR using the review loop; rerun the fast gate after any review-driven edit.
