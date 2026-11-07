# Multi-phase playbook

## Objective

Land independently proven increments behind a flag, not long-lived or stacked branches.

## Steps

1. **Frame**: define end-to-end criteria and split independently verifiable units, each with
   outcome, dependencies, criteria and one PR. State a flag removal criterion; use `kiss`
   to cut criteria/questions. Read `skill://codegraph/references/readiness-and-bootstrap.md`
   before discovery, then `skill://codegraph/references/query-selection.md` to map owners,
   callers and dependency order. A unit waits only when it actually builds on another.
2. **Spike**: for unanswered plan-changing questions, read `skill://git-tool` and
   `skill://omp-dyna-workflows/references/patterns.md`; declare predicates, side effects
   and timebox. Non-isolated `task` uses absolute paths in a detached scratch worktree;
   report commands, outputs and failures, then discard all spike code. Prototype race only
   when built results distinguish options; council only when no prototype can settle it.
3. **Plan**: read `skill://omp-dyna-workflows/references/patterns.md`,
   `skill://test-guidance/references/tiers.md` and
   `skill://test-guidance/references/suite-budget.md`; map every unit's criteria to named
   manifest proofs, missing proof surfaces first. Use `defect` plus `expectRed` for fixes,
   `preserve` for refactors, `new-surface` for absent APIs. Keep plan draft until spike ends.
   Read `skill://coding-guidance/references/clean-cutovers.md` for replacing units and
   `skill://coding-guidance/references/regression-coverage.md` for defect-class coverage.
4. **Implement**: require `.omp/gate.json` `checks.commit` and `pathCheck`; otherwise read
   `skill://setup-repository-guardrails` first. Read `skill://git-tool` and
   `skill://git-tool/references/branch-and-integrate.md`; one writer per short-lived
   `agent/<slug>` branch from default, never spike. Parallelize only independent units with
   explicit interfaces. Read `skill://coding-guidance/references/implementation-integrity.md`,
   `skill://coding-guidance/references/structure-and-grouping.md` and
   `skill://coding-guidance/references/naming.md` before edits/new or moved paths/names.
   Per unit, spawn proof `task`, commit proofs through the hook, main calls `freeze()`, then separately
   spawn implementation `task`. Land green increments behind the flag; migrate callers and
   delete obsolete paths in the canonical cutover unit. Read
   `skill://coding-guidance/references/verification.md`; commits run `checks.commit` via the hook.
5. **Verify**: read `skill://git-tool/references/verify-loop.md`; run `prove()` per
   code-ready unit, proofs green twice at head and parent evidence by kind. Cross-family
   verifier drives criteria and checks affected docs under the blocking rule; prove combined
   end-to-end criteria before flag removal.
6. **Integrate**: read `skill://git-tool/references/branch-and-integrate.md` and
   `skill://git-tool/references/merge-and-recover.md`; CI green on exact head, rebase-merge
   each unit with `--match-head-commit`, wait for default CI. Target same-day merge; no stacked
   or long-lived branches. Re-verify fixes/rebases once at new head per verify loop. Remove the
   flag when its declared criterion is met.
