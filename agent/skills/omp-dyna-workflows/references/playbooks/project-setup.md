# Project setup playbook

## Objective

Establish owner-approved repository guardrails and a shared local/CI gate contract.

## Steps

1. Identify the repository, chosen stack, setup outcome, and existing work to preserve.
2. Read `skill://codegraph/references/readiness-and-bootstrap.md` before repository discovery.
3. Read `skill://codegraph/references/query-selection.md`; inventory existing controls and owners.
4. Read `skill://git-tool`, then its branch-and-integrate runbook; allocate one writer branch.
5. Read `skill://coding-guidance/references/implementation-integrity.md` before the first edit.
6. Read `skill://coding-guidance/references/structure-and-grouping.md` before creating paths.
7. Read `skill://coding-guidance/references/naming.md` before naming files, folders, or commands.
8. Read `skill://setup-repository-guardrails`; execute its inventory, rule mapping, candidate,
   and dry-run stages; honour its STOP for explicit owner approval before applying changes.
   Delegate every code change, including candidate implementations and small fixes, to `task`.
9. Read `skill://setup-repository-guardrails`; apply only approved candidates through `task`;
   ensure `.omp/gate.json` exists with `schemaVersion: 1` and the repository's fast gate.
   Wire the authoritative gate into local and CI paths; remove only approved replaced controls.
10. Read `skill://coding-guidance/references/verification.md`; prove the guardrails and run the
    repository fast gate from `.omp/gate.json` after the final edit; record the result.
11. Read `skill://git-tool/references/review-loop.md`; obtain independent-session,
    cross-family review per the owner rules and resolve findings through `task`.
12. Read `skill://git-tool/references/branch-and-integrate.md`; integrate the verified
    unit as one PR using the review loop; rerun the fast gate after any review-driven edit.
