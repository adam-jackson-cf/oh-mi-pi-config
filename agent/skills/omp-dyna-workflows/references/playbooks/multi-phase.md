# Multi-phase playbook

## Objective

Deliver a larger engineering outcome as independently verifiable units with one PR per unit.

## Steps

1. Define the end-to-end acceptance criteria and split the work into verifiable units;
   give each unit its own observable result, dependencies, acceptance criteria, and one PR.
2. Read `skill://codegraph/references/readiness-and-bootstrap.md` before repository discovery.
3. Read `skill://codegraph/references/query-selection.md`; map owners, callers, and dependency order.
4. Read `skill://git-tool`, then its branch-and-integrate runbook; assign one writer per
   branch and one branch per unit; parallelize only independent units with explicit interfaces.
5. Read `skill://coding-guidance/references/implementation-integrity.md` before each unit's
   first edit; delegate every code change, including small fixes and tests, to `task`.
6. Read `skill://coding-guidance/references/structure-and-grouping.md` for new or moved paths.
7. Read `skill://coding-guidance/references/naming.md` before introducing or renaming concepts.
8. Read `skill://coding-guidance/references/clean-cutovers.md` for units that replace or refactor;
   migrate all callers and remove obsolete paths in the unit that makes the cutover canonical.
9. Read `skill://coding-guidance/references/regression-coverage.md` for bug-fix units; require
   reproduction and a representative regression that fails before the fix and passes afterward.
10. Read `skill://test-guidance/references/suite-budget.md`; justify each unit's test additions.
11. Read `skill://coding-guidance/references/verification.md`; for each completed unit, run the
    repository fast gate from `.omp/gate.json` after its final edit and record the result.
12. Read `skill://git-tool/references/review-loop.md`; obtain independent-session,
    cross-family review per the owner rules for each unit; resolve findings through `task`.
13. Read `skill://git-tool/references/branch-and-integrate.md`; integrate one verified
    unit per PR in dependency order using the review loop; rerun the fast gate after review edits
    and verify the combined acceptance criteria before handing off the full outcome.
