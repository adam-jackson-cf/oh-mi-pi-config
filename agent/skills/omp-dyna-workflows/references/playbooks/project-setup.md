# Project setup playbook

## Objective

Establish approved guardrails with canary proofs and one shared local/CI gate contract.

## Steps

1. **Frame**: identify stack, setup outcome, existing work and acceptance criteria;
   use `kiss` to cut criteria and plan-changing questions. Read
   `skill://codegraph/references/readiness-and-bootstrap.md` before discovery, then
   `skill://codegraph/references/query-selection.md` to inventory controls and owners.
2. **Spike**: read `skill://setup-repository-guardrails` for inventory, rule mapping,
   candidates and dry runs. Read `skill://git-tool` and
   `skill://omp-dyna-workflows/references/patterns.md` for remaining questions: declared
   predicates/side effects/timebox, detached scratch worktree, non-isolated `task` with
   absolute paths. Report evidence and discard spike code. Honour guardrails' STOP for
   explicit owner approval before applying controls.
3. **Plan**: read `skill://omp-dyna-workflows/references/patterns.md` and
   `skill://test-guidance/references/tiers.md`; map criteria to guardrail canary proofs
   (intentional violations rejected and valid inputs accepted). Fill canary driver/fixture
   gaps first; use `new-surface` for new controls and `preserve` for existing controls.
   Keep plan draft until spike answers land.
4. **Implement**: read `skill://git-tool/references/branch-and-integrate.md`; branch
   `agent/<slug>` from default, never spike. Read
   `skill://coding-guidance/references/implementation-integrity.md`,
   `skill://coding-guidance/references/structure-and-grouping.md` and
   `skill://coding-guidance/references/naming.md` before edits/paths/names.
   Spawn proof `task`, commit proofs through the hook, main calls `freeze()`, then separately spawn
   implementation `task`. Read `skill://setup-repository-guardrails`; apply only approved
   controls, establish `.omp/gate.json` with `schemaVersion: 2`, `checks.commit`, `checks.ci`,
   `pathCheck` and an installed pre-commit hook as the prerequisite for ordinary
   implementation. Wire `checks.commit` into the hook and `checks.ci` into CI on `ciHost`;
   remove only approved replaced controls. Read
   `skill://coding-guidance/references/verification.md`; commits run `checks.commit` via the hook.
5. **Verify**: read `skill://git-tool/references/verify-loop.md`; run `prove()` with
   canaries green twice at head, parent checks by kind and a cross-family live verifier.
   List guardrail/gate-file changes under **Gate changes** in the PR description.
6. **Integrate**: read `skill://git-tool/references/branch-and-integrate.md` and
   `skill://git-tool/references/merge-and-recover.md`; CI green on exact head, rebase-merge
   with `--match-head-commit`, confirm `MERGED`, record merge-SHA default CI (NUC: submit), and
   continue without waiting. Before the next merge, check latest default CI: success permits;
   pending waits (NUC: wait); failure/cancellation stops for red-main recovery, which still waits
   for green. Fixes/rebases receive one verification on new head per the verify loop.
