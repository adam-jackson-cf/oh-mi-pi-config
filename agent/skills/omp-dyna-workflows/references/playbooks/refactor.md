# Refactor playbook

## Objective

Make the target design canonical, preserve required behaviour and migrate every caller.

## Steps

1. **Frame**: define outcome, preserved behaviours, acceptance criteria and plan-changing
   questions; use `kiss` to cut scope. Read
   `skill://codegraph/references/readiness-and-bootstrap.md` before discovery, then
   `skill://codegraph/references/query-selection.md`; inventory every caller and owner.
2. **Spike**: if questions remain, read `skill://git-tool` and
   `skill://omp-dyna-workflows/references/patterns.md`; declare predicates, side effects and
   timebox. Non-isolated `task` uses absolute paths in a detached scratch worktree; report
   commands, outputs and failures, then discard it. Never branch implementation from spike.
3. **Plan**: read `skill://coding-guidance/references/clean-cutovers.md`,
   `skill://test-guidance/references/tiers.md`, `skill://test-guidance/references/suite-budget.md`
   and `skill://omp-dyna-workflows/references/patterns.md`. Map preserved criteria to
   `kind: preserve` proofs (green at parent and head); fill proof-surface gaps first.
   Plan removal of obsolete implementations, aliases, comments and compatibility paths;
   keep plan draft until spike answers land.
4. **Implement**: require `.omp/gate.json` `checks.commit` and `pathCheck`; otherwise read
   `skill://setup-repository-guardrails` first. Read `skill://git-tool` and
   `skill://git-tool/references/branch-and-integrate.md`; branch `agent/<slug>` from default.
   Read `skill://coding-guidance/references/implementation-integrity.md`,
   `skill://coding-guidance/references/structure-and-grouping.md` and
   `skill://coding-guidance/references/naming.md` before edits/moves/renames.
   Proof `task` writes uncommitted files; main calls `freeze()` before implementation `task`.
   Both run non-isolated, sequentially, with absolute paths in the dedicated agent worktree.
   Implementation task commits proofs with their greening change (default: one atomic commit);
   main never commits. No proof enters history earlier. Split only if every earlier commit
   passes the hook with all proof files present. Migrate callers/tests/docs; remove obsolete paths.
   Read `skill://coding-guidance/references/verification.md`; commits run `checks.commit` via the hook.
5. **Verify**: read `skill://git-tool/references/verify-loop.md`; run `prove()` with
   preservation proofs green on parent overlay and twice at head. Cross-family verifier
   drives every criterion and checks affected docs; apply only the defined blocking rule.
6. **Integrate**: read `skill://git-tool/references/branch-and-integrate.md` and
   `skill://git-tool/references/merge-and-recover.md`; require CI green on exact head,
   rebase-merge with `--match-head-commit`, confirm `MERGED`, record merge-SHA default CI
   (NUC: submit), and continue without waiting. Before the next merge, check latest default CI:
   success permits; pending waits (NUC: wait); failure/cancellation stops for red-main recovery,
   which still waits for green. Fix/rebase gets one verification at new head per the verify loop.
