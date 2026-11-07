# Bug fix playbook

## Objective

Repair the reproduced failure class and prove parent red for the right reason, head green twice.

## Steps

1. **Frame**: capture reported and expected behaviour, criteria and plan-changing questions;
   use `kiss` to cut scope. Read `skill://codegraph/references/readiness-and-bootstrap.md`
   before discovery and `skill://codegraph/references/query-selection.md` to trace ownership.
2. **Spike**: the reproduction is the spike. Read `skill://git-tool` and
   `skill://omp-dyna-workflows/references/patterns.md`; declare a failure predicate, side
   effects and timebox; use a detached scratch worktree and non-isolated `task` with absolute
   paths. Preserve commands/outputs/failures in the spike report, discard all spike code.
   Read `skill://test-guidance/references/flakes.md` for intermittent failures; control causes,
   never suppress or retry away evidence.
3. **Plan**: read `skill://coding-guidance/references/regression-coverage.md`,
   `skill://test-guidance/references/tiers.md`, `skill://test-guidance/references/suite-budget.md`
   and `skill://omp-dyna-workflows/references/patterns.md`. Map representative defect-class
   criteria to `kind: defect` proofs with nonempty `expectRed`; fill proof-surface gaps first.
   Keep the plan draft until reproduction answers the open questions.
4. **Implement**: require `checks.commit` and `pathCheck` in `.omp/gate.json`; otherwise read
   `skill://setup-repository-guardrails` first. Read `skill://git-tool` and
   `skill://git-tool/references/branch-and-integrate.md`; branch `agent/<slug>` from default,
   never spike. Read `skill://coding-guidance/references/implementation-integrity.md`.
   Spawn proof `task`, commit proofs through the hook, main calls `freeze()`, then separately spawn
   implementation `task` for the root cause. Read
   `skill://coding-guidance/references/verification.md`; commits run `checks.commit` via the hook.
5. **Verify**: read `skill://git-tool/references/verify-loop.md`; run `prove()`.
   Parent overlay must fail containing `expectRed`, not just any failure; head passes twice.
   Cross-family verifier drives criteria live and checks affected docs under the blocking rule.
6. **Integrate**: read `skill://git-tool/references/branch-and-integrate.md` and
   `skill://git-tool/references/merge-and-recover.md`; CI green on exact head, rebase-merge
   with `--match-head-commit`, wait for default CI. Re-verify a fix/rebase once on the new head
   as specified by the verify loop.
