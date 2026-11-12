# Branch and integrate

One verified unit = one `agent/<slug>` branch = one PR. Units target the resolved default unless the repository's
project instructions document a target/merge-method exception.
Authority and safety belong to [git-tool](../SKILL.md).

## Main prepares the unit

1. Inspect status, diff, recent history and remotes; preserve unrelated work and secrets.
2. Identify the existing primary remote and its host. Not GitHub: stop before pushing and ask the owner.
   No remote: report the missing prerequisite; never invent one.
3. Resolve the default once with `gh repo view --json defaultBranchRef -q .defaultBranchRef.name`,
   or the primary remote's symbolic HEAD (for origin, `git symbolic-ref refs/remotes/origin/HEAD`).
   Record `<remote>` and `<default>`; never assume `main`. Use this resolution for comparisons, PR targets and recovery.
4. Read the repo's record `gate.json` ([git-tool](../SKILL.md#task) defines the record). Invalid JSON, unsupported schema,
   invalid fields or a `dynaflowsContract` below `agent/agent-rails.yml` fail closed.
   `agentMerge: disabled` requires owner approval for this workflow; absent field defaults to allowed, not permission to skip prerequisites.
5. Ordinary implementation requires a record with valid schema-v2 `checks.commit` and `pathCheck`.
   No record or missing controls route to [dynaflows SKILL's owner-approved project-setup prerequisite](skill://omp-dyna-workflows/SKILL.md).
   Follow its link to `setup-repository-guardrails` for record draft and approval mechanics, not autonomous installation.
   Bootstrap may propose the record and repo fixes without already having the completed gate.
   Complete that prerequisite before resuming ordinary work. No-CI is not a bootstrap waiver.
6. Fetch the primary remote before creating the dedicated worktree/branch from `<remote>/<default>` (not stale local default or a spike).
   Supply each task the absolute worktree and assigned file paths. Keep one writer per branch.
   The [stage-4 contract](skill://omp-dyna-workflows/SKILL.md) owns task ordering, proof freezing and atomic proof/fix history;
   do not run isolated patch-return modes or have main apply implementation patches.
7. A nested Git repository change is its own named repository/unit, assigned writer, branch, PR and verification.
   Land that PR first; the parent pin-update PR depends on it and records its repository and PR URL in the description.
   Never silently omit nested changes.

## Implementation task publishes

Proof tasks stop uncommitted; only the assigned implementation task performs these steps.

1. Review the final diff and stage only intended files. Create new logical commits via `git commit`.
   Rails run the record's `checks.commit` and then the harness checks over the whole repo; any finding blocks the commit.
   Never disable the repo's own hooks with flags, configuration or hook-skip environment variables.
   Use the [skill's commit vocabulary and safety rules](../SKILL.md#git-safety).
   CI runs `checks.ci`; do not add pre-push or handoff suite runs.
2. Push the branch to the resolved GitHub primary remote, adding upstream tracking when needed.
   No routine rebase is needed; [merge-and-recover](merge-and-recover.md) owns rebase conditions and history permissions.
3. Read [PR description](pr-description.md) and write the complete body. Use existing reported evidence only;
   unavailable comparable measurements are `baseline unavailable`. `prove()` later posts the verification result and
   main a short judgement comment, both separate from the body.
4. Open the actual PR with the prepared body, concise title, resolved base and agent branch head.
   Use GitHub `pr_create` when available, otherwise authenticated `gh pr create`; never use `fill` instead of the body.
5. Submit and wait for exact-head CI under [the CI contract](merge-and-recover.md#ci-waits-errors-and-fallbacks).
   Repair genuine red checks as the writer; infrastructure incidents follow that contract, never a gate waiver.
6. Return the created PR URL, exact head, CI evidence, description and outstanding limitations, with no pending writer edits.
   This is the ready-PR handoff. Main continues with [verify-loop](verify-loop.md), without asking for merge permission.
