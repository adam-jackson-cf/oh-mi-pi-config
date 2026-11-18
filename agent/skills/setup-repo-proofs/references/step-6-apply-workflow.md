# Approved application

## Objective

Land only approved changes, preserving unrelated work.

## Guidance

1. Recheck target Git status and the approved base ref. Use an isolated worktree or equivalent
   reversible checkout for unrelated work or overlapping edits.
2. Apply approved paths only. Merge surgically or replace only by explicit decision. Remove
   superseded runner/hook policies and duplicate authorities; explain when there is no predecessor.
   Never remove an authoritative project checker in favor of a duplicate.
3. Deliver checker extensions through normal PRs with green checks. Setup is incomplete until all
   prerequisite checker changes merge. Pin project tools with the project's package manager;
   keep roots, exclusions, allowlists, ownership/naming maps and CI wiring explicit.
4. Use a tracked hook manager or native `.githooks/pre-commit` when the project wants a hook;
   `git config core.hooksPath .githooks` enables the latter. Invoke the commit command and propagate
   nonzero status. No pre-push hook or separate handoff gate. Auto-fix stays explicit and outside
   check-only CI. Full fast/boundary CI runs on push and PR with an unconditional all-success
   aggregate if parallel; scheduled exploratory work remains outside those gates.
5. Require the declared CI status green on the exact PR head under the governing Git authorization.
   Do not infer merge permission here. Do not configure host branch protection: that is a separate,
   optional owner step. Never change runner roles to evade missing registration or credentials;
   report provisioning blockers.
6. Keep ordinary tests for project checkers near their owner. Update contributor and application-user
   documentation separately. Inspect proposed publication for out-of-scope edits, secrets and
   generated output. Preserve historical ADRs and digest-pinned study/evaluation artifacts.

## Done when

Changes match the approved matrix, unrelated work is intact, superseded authorities are removed
and prerequisite PRs are complete under the existing Git/PR authorization.
