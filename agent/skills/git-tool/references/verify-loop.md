# Verify loop

The implementation task returns a ready PR: no pending writer edits, complete writer-authored description,
and required CI green for its exact head. Main runs the PR check and merges. Main never patches or commits;
proof tasks do not publish PRs.

## Canonical contracts

- Six-stage ordering and task briefs: [dynaflows SKILL](skill://omp-dyna-workflows/SKILL.md).
- Helper signatures, manifest schema and the result file: [patterns](skill://omp-dyna-workflows/references/patterns.md).
- PR-head CI waits, infrastructure incidents and no-CI eligibility:
  [merge-and-recover](merge-and-recover.md#ci-waits-errors-and-fallbacks).

## The PR check

With dynaflows off, the PR check is required CI green on the exact head. With dynaflows on, it is `prove()`:

1. Main obtains the actual PR base branch and exact 40-hex head from GitHub, using the primary remote/default already
   resolved by [branch-and-integrate](branch-and-integrate.md). Fetch the actual target and record its remote-tracking
   ref as `target_ref`. Confirm that GitHub's PR target matches; the helper's range check cannot authenticate that metadata.
2. Set `parent` to `git merge-base <target_ref> <head>`, never `HEAD~1`. It must be a strict ancestor of head.
3. Main calls `prove(repo, parent, head, manifest_path, manifest_sha256, objective, target_ref=target_ref)`.
   `repo` is the dedicated source worktree on its named branch at head, not main's possibly different checkout.
   A PR with no behaviour change (refactor, docs, project setup without new controls, a Dependabot bump) uses a manifest
   frozen with no proofs; `prove()` then checks CI green only.
4. `prove()` checks CI green on the exact head and that every frozen proof is unchanged and passes there. It writes
   `<git-common-dir>/omp-prove/<head>.result.json` and posts the result as a PR comment; main never copies it by hand.
5. On `fail`, return the blocking evidence (commands, output, expected outcome) to the assigned writer.
   Infrastructure failures fail closed; they are not red proof assertions.

Never write or edit a result file by hand. Main proceeds to [merge-and-recover](merge-and-recover.md).

## Changed heads and fix rounds

The result file is bound to its head. After a fix, a rebase or a merge of the target, the writer pushes and waits for
exact-head CI; main then calls `prove()` once on the new head. That is the only extra step: no re-freeze (unless a proof
file changed), no other check.

A fix round is one writer repair cycle returning a new head after a failed `prove()` or red CI. After three rounds,
unresolved failures stop the unit: escalate with PR/head, failed checks, attempted fixes and decisions needed.
