# Branch and integrate

One unit = one `agent/<slug>` branch = one PR. Units target the resolved default unless the repository's
project instructions document a target/merge-method exception.
Authority and safety belong to [authority-and-safety](authority-and-safety.md).
This runbook applies in both dynaflows modes; steps marked **Structured mode only** apply only in structured mode.

## Main prepares the unit

1. Inspect status, diff, recent history and remotes; preserve unrelated work and secrets.
2. Identify the existing primary remote and its host. Not GitHub: stop before pushing and ask the owner.
   No remote: report the missing prerequisite; never invent one.
3. Resolve the default once with `gh repo view --json defaultBranchRef -q .defaultBranchRef.name`,
   or the primary remote's symbolic HEAD (for origin, `git symbolic-ref refs/remotes/origin/HEAD`).
   Record `<remote>` and `<default>`; never assume `main`. Use this resolution for comparisons, PR targets and recovery.
4. Read the repo's record `gate.json` when present ([record contract](../../../record/README.md)).
   Invalid declared policy fails closed; an absent record does not require setup.
5. Fetch the primary remote before creating the dedicated worktree/branch from `<remote>/<default>` (not stale local default).
   Create it at `<worktree.base>/<repo>/<slug>` (`~/worktrees/<repo>/<slug>`), where `<repo>` is the
   `origin` remote's repo name in lowercase and `<slug>` is the branch slug from `agent/<slug>`; rails refuse any other `git worktree add` target.
   Supply each task the absolute worktree and assigned file paths. Keep one writer per branch.
6. A nested Git repository change is its own named repository/unit, assigned writer, branch, PR and verification.
   Land that PR first; the parent pin-update PR depends on it and records its repository and PR URL in the description.
   Never silently omit nested changes.

## Assigned writer publishes

The assigned writer performs these steps:

1. Review the final diff and stage only intended files. Create new logical commits via `git commit`.
   Run the configured harness commit checks when applicable.
   Never disable the repo's own hooks with flags, configuration or hook-skip environment variables.
   Use the [commit vocabulary and safety rules](authority-and-safety.md#git-safety).
   CI uses `checks.ci` when declared; do not add pre-push or handoff suite runs.
2. Push the branch to the resolved GitHub primary remote, adding upstream tracking when needed.
   No routine rebase is needed; in structured mode [merge-and-recover](merge-and-recover.md) owns rebase conditions.
   Never push to the default branch.
3. Read [PR description](pr-description.md) and write the complete body. Use existing reported evidence only;
   unavailable comparable measurements are `baseline unavailable`.
4. Open the actual PR with the prepared body, concise title, resolved base and agent branch head.
   Use GitHub `pr_create` when available, otherwise authenticated `gh pr create`; never use `fill` instead of the body.
5. **Structured mode only**: submit and wait for exact-head CI under [the CI contract](merge-and-recover.md#ci-waits-errors-and-fallbacks).
   Repair genuine red checks as the writer; infrastructure incidents follow that contract, never a gate waiver.
6. Return the created PR URL, exact head, description and outstanding limitations, with no pending writer edits;
   structured mode adds the CI evidence. This is the ready-PR handoff.
   In minimal mode the owner merges. In structured mode main continues with [merge-and-recover](merge-and-recover.md),
   without asking for merge permission.
