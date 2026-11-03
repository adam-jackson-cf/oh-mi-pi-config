# Refactor playbook

## Objective

Make the target design canonical while preserving required behaviour and migrating every caller.

## Steps

1. Define the target design, preserved behaviours, and observable completion criteria.
2. Read `skill://codegraph/references/readiness-and-bootstrap.md` before repository discovery.
3. Read `skill://codegraph/references/query-selection.md`; inventory all callers and affected owners.
4. Read `skill://git-tool`, then its branch-and-integrate runbook; allocate one writer branch.
5. Read `skill://coding-guidance/references/implementation-integrity.md` before the first edit.
6. Read `skill://coding-guidance/references/clean-cutovers.md`; plan the canonical cutover and
   removal of obsolete implementations, aliases, comments, and compatibility paths.
7. Read `skill://coding-guidance/references/structure-and-grouping.md` before files or folders move.
8. Read `skill://coding-guidance/references/naming.md` before renaming concepts or paths.
9. Delegate every code change to `task`; migrate every caller, test, and affected document,
   remove the obsolete path, and prove preserved behaviour without compatibility shims.
10. Read `skill://coding-guidance/references/verification.md`; run the repository fast gate from
    `.omp/gate.json` after the final edit and record the result before handoff.
11. Read `skill://git-tool/references/review-loop.md`; obtain independent-session,
    cross-family review per the owner rules and resolve findings through `task`.
12. Read `skill://git-tool/references/branch-and-integrate.md`; integrate the verified
    unit as one PR using the review loop; rerun the fast gate after any review-driven edit.
