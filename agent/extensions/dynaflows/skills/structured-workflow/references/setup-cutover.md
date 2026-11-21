# Harness setup: Steps 6–7 cutover and verification deltas

## Objective

Save the approved record, hand each step to its owner and prove the commit, CI and merge wiring.

## Step 6: authority cutover

1. A repo checker named in `guardrails.check.repo` is the authority for its step; never remove it in favour of the
   harness step. Remove approved superseded runner and hook policies and any omp-specific files.
2. Save the approved record before the `grow` PRs are committed, so stale harness settings never block the commits that
   replace them. It gives every step whose checker files the repo already lists to the repo.
3. A `grow` step whose checker the PR creates stays a harness step until that PR merges, because `check` rejects a
   checker path the repo does not list; the owner then moves it to `guardrails.check.repo`.
4. Setup is not done until every `grow` PR has merged with green checks and the record names every repo checker.
   Repo changes land through a normal PR.
5. Save the confirmed record as `repos/<owner>/<name>/` under the oh-mi-pi-config root. Write the live record yourself;
   never ask the owner to save a file. Never commit, stage or copy a record into the target.
6. The live `profile.md` starts from [the record profile template](../../../record/profile.md) with its front
   matter and prose filled. Run `guardrails init --repo <worktree> --record <draft dir>` from the harness, write the
   confirmed values into the draft, and compare it with the approved matrix before saving. Preserve existing profile
   values when extending it. `guardrails profile-check` must pass (`PROFILE.complete`).
7. The `dynaflows` tool or directive sets the repo's mode in `modes.json` when the owner asks.
   Write approval fields only as the owner stated them in the chat; the Step 5 STOP stays the boundary before applying repo fixes.
8. Pin tools the repo runs through the target's package manager. Keep repo commit and CI commands unchanged.
9. Execution events follow [the record contract](../../../record/README.md#process-mode-and-events).
   Any commit finding blocks, including drift from contributors who do not use omp. Merge needs `ci.requiredContext`
   green on the exact head and, in structured mode, a `prove()` pass on that head. T3 runs on a schedule, outside the
   record's checks.
10. A repo hook runs `checks.commit` only when the project wants one.
11. Do not configure host branch protection; rails check exact-head `ci.requiredContext` before merge.

## Step 7: engine and integration verification

1. Run the harness checks over the whole repository with the record, as rails do at commit:
   `uv run --project <omp root>/agent/extensions/dynaflows/engine guardrails check --repo <worktree> --record <record dir>`.
   Expect exit 0 with zero findings outside the record's scope exclusions, JUnit and temporary output outside the
   worktree, and no file written into it.
2. For each repo-owned checker, including every checker named in `guardrails.check.repo`, run its own tests; `check`
   reports a named checker file the repository does not list. For `STRUCT.dependency-direction`,
   `STRUCT.folder-placement`, `NAME.conventions` and `TEST.placement` implemented by the repo's own path command,
   assert that each failure names the file and the exact corrective action.
3. Prove the test-evidence wiring: the harness runs the budget checker at the agent's commit and blocks on its failure,
   and `checks.ci` never runs it. Prove machine selection against the approved limits; an unapproved machine prompts for
   a baseline and reports timing only.
4. Parse the record's `gate.json` against [the gate schema](../../../record/README.md#gate-schema); optional fields stay
   optional to the format validator. In an isolated checkout, a passing `checks.commit` with zero harness findings permits
   an agent commit, and a failing one blocks it.
5. Confirm `git status` in the target shows no record, proposal, JUnit or other harness output, and that
   `git grep -n -i -w -E 'omp|guardrails|dynaflows'` finds no harness mention the change added.
   Rerun the harness checks after repairs.
