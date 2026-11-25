# Verify loop

## Objective

Define the ready PR a writer returns and the fix rounds that follow a failed check.

## Guidance

- The implementation task returns a ready PR: no pending writer edits, a complete writer-authored description and
  required CI green for its exact head. Proof tasks do not publish PRs.
- Main runs the PR check of [Verify](step-5-verify-workflow.md) and merges under [Integrate](step-6-integrate-workflow.md).
- PR-head CI waits, infrastructure incidents and no-CI eligibility follow
  [merge-and-recover](skill://git-tool/references/merge-and-recover.md#ci-waits-errors-and-fallbacks).
- A result file is bound to its head. After a fix, a rebase or a merge of the target, the writer pushes and waits for
  exact-head CI; main then calls `prove()` and `structure_review()` on the new head.
- No re-freeze is needed after a head change unless a proof file changed or a discovery added a proof.
- A map-coverage block returns to the writer: add the outcome test, link it from the changed map doc, and main re-freezes.
- Grade each review or gap finding that proposes a test, edge case, guard or handling path against the case rules
  (`skill://test-guidance/assets/templates/installed/references/writing-tests.md#choosing-cases`) before a fix round acts on it.
  Act on keep and extend-existing;
  report each drop with its failing criterion and evidence. Failed `prove()`, CI and `halt` results are not graded.
- A fix round is one writer repair cycle returning a new head after a failed `prove()` or red CI.
- After three fix rounds, unresolved failures stop the unit: escalate with the PR and head, failed checks,
  attempted fixes and decisions needed.
