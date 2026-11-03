# Feature playbook

## Objective

Deliver one verified feature through a delegated implementation and independently reviewed PR.

## Steps

1. Define acceptance criteria and the smallest verifiable feature unit.
2. Read `skill://codegraph/references/readiness-and-bootstrap.md` before repository discovery.
3. Read `skill://codegraph/references/query-selection.md`; trace the affected owners and callers.
4. Read `skill://git-tool`, then its branch-and-integrate runbook; allocate one writer branch.
5. Read `skill://coding-guidance/references/implementation-integrity.md` before the first edit.
6. Read `skill://coding-guidance/references/structure-and-grouping.md` for new files or folders.
7. Read `skill://coding-guidance/references/naming.md` before introducing names.
8. Read `skill://test-guidance/references/tiers.md`; choose coverage for the acceptance criteria.
9. Read `skill://test-guidance/references/suite-budget.md` before adding test-suite cost.
10. Delegate every code change, including tests and small fixes, to `task`; implement the criteria.
11. Read `skill://coding-guidance/references/verification.md`; run the repository fast gate from
    `.omp/gate.json` after the final edit and record the result before handoff.
12. Read `skill://git-tool/references/review-loop.md`; obtain independent-session,
    cross-family review per the owner rules and resolve findings through `task`.
13. Read `skill://git-tool/references/branch-and-integrate.md`; integrate the verified
    unit as one PR using the review loop; rerun the fast gate after any review-driven edit.
