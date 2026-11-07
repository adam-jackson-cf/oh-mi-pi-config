# Feature playbook

## Objective

Deliver one feature with frozen acceptance proofs and cross-family live verification.

## Steps

1. **Frame**: define outcome, acceptance criteria and only plan-changing open questions;
   use `kiss` to cut criteria and questions. Read
   `skill://codegraph/references/readiness-and-bootstrap.md` before discovery, then
   `skill://codegraph/references/query-selection.md`; trace owners and callers.
2. **Spike**: if questions remain, read `skill://git-tool` and
   `skill://omp-dyna-workflows/references/patterns.md`; declare predicates, side effects and
   timebox. Use a detached scratch worktree and non-isolated `task` with absolute paths;
   report commands, outputs and failures, then discard the worktree, never publish its code.
3. **Plan**: read `skill://omp-dyna-workflows/references/patterns.md`;
   map each criterion to a named manifest proof (`new-surface` for absent APIs).
   Make missing proof surfaces first tasks; keep plan draft until spike answers land.
   Read `skill://test-guidance/references/tiers.md` and
   `skill://test-guidance/references/suite-budget.md` before selecting coverage.
   Passing-at-baseline freeze returns to Plan: criterion already met (report, no PR) or reuse
   existing tracked tests as preserve; never weaken a proof into red.
4. **Implement**: require `.omp/gate.json` with `checks.commit` and `pathCheck`, otherwise read
   `skill://setup-repository-guardrails` first. Read `skill://git-tool` and
   `skill://git-tool/references/branch-and-integrate.md`; start `agent/<slug>` from default,
   never spike. Read `skill://coding-guidance/references/implementation-integrity.md`,
   `skill://coding-guidance/references/structure-and-grouping.md` and
   `skill://coding-guidance/references/naming.md` before edits/new paths/names.
   Proof `task` writes uncommitted files; main calls `freeze()` before implementation `task`.
   Both run non-isolated, sequentially, with absolute paths in the dedicated agent worktree.
   Implementation task commits proofs with their greening change (default: one atomic commit);
   main never commits. No proof enters history earlier. Split only if every earlier commit
   passes the hook with all proof files present. Read
   `skill://coding-guidance/references/verification.md`; commits run `checks.commit` via the hook.
5. **Verify**: read `skill://git-tool/references/verify-loop.md` and
   `skill://omp-dyna-workflows/references/patterns.md`; run `prove()` at code-ready head.
   Require green-twice head proofs and parent evidence by kind, then the cross-family verifier
   drives every criterion and checks affected docs; only its defined blocking rule blocks.
6. **Integrate**: read `skill://git-tool/references/branch-and-integrate.md` and
   `skill://git-tool/references/merge-and-recover.md`; require CI green on exact head,
   rebase-merge with `--match-head-commit`, confirm `MERGED`, record merge-SHA default CI
   (NUC: submit), and continue without waiting. Before the next merge, check latest default CI:
   success permits; pending waits (NUC: wait); failure/cancellation stops for red-main recovery,
   which still waits for green. Fixes/rebases receive one verification on the new head per
   the verify loop.
