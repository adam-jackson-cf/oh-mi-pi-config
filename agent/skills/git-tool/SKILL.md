---
name: "git-tool"
description:
  "Use when the task requires any Git action: branching, committing, pushing,
  opening, describing or verifying a pull request, merging, or recovering a
  red default branch."
hide: true
disable-model-invocation: true
---

# Task

A repo's **record** is its personal harness config: `repos/<owner>/<name>/` under the oh-mi-pi-config root, named by
the clone's `origin` remote, gitignored and never committed. It holds `gate.json` (commands, `pathCheck`, `ci`, `agentMerge`,
`dynaflows.enabled`; `pathCheck` and `checks.commit` are required) and `profile.md`. The repo itself holds nothing about omp or dynaflows.

The orchestrating (main) agent owns each change from branch to merge; the owner reviews merged PRs afterwards when he chooses.
Implementation tasks commit, push, write the description, open the PR and wait for exact-head green CI before handing off.
Proof tasks leave files uncommitted and never stage, commit, push or open PRs.
With dynaflows on, main runs `prove()` on the PR head (it posts its own result) and merges; main never commits or writes the PR body.
Read before any Git action. Where this is silent: keep existing content, make no irreversible write,
ask the user once when the choice changes the outcome, state the choice in the result.

## Model and owner authority

- Pando model: one unit per `agent/<slug>` branch, one writer per branch, one PR per unit.
  Main runs the PR check, coordinates fixes and merges; implementation tasks commit through the commit checks and deliver ready PRs. Main never commits.
  Only same-branch writes contend: independent units run and merge independently.
  - Short-lived `agent/*` branches are the trunk-based unit; target same-day merge. A unit waits only on a unit it builds on.
  - Every agent commit is checked: rails run the record's `pathCheck`, its `checks.commit` and then the harness
    checks over the whole repo. Never bypass them or the repo's own hooks.
  - The proof/implementation task sequence belongs to
    [dynaflows SKILL stage 4](skill://omp-dyna-workflows/SKILL.md).
  - Land multi-phase work incrementally behind a flag with a stated removal criterion; never long-lived or stacked branches.
- Helper contracts: [patterns](skill://omp-dyna-workflows/references/patterns.md). Main runs the PR check through
  [verify-loop](references/verify-loop.md).
- Granted in every repository unless its record's `gate.json` has `"agentMerge": "disabled"`: create `agent/*` branches, commit,
  push them to the existing GitHub primary remote, open PRs, and merge them once required CI (`checks.ci`) is green on the exact head and, with dynaflows on,
  `prove()` passed on that head. This grant overrides recommend-before-acting for these actions only.
  CI exceptions and incident handling belong to [merge-and-recover](references/merge-and-recover.md#ci-waits-errors-and-fallbacks);
  missing protection alone never waives existing CI.
- Remote/default resolution belongs to [branch-and-integrate](references/branch-and-integrate.md).
  Rebase conditions, force-with-lease and any project-documented merge-method exception belong to
  [merge-and-recover](references/merge-and-recover.md).
- Pulling existing commits into a branch (merge the target, rebase, cherry-pick, revert, sync) needs no proof and
  no extra checks: those commits were checked when they were made.
- The repo's record (policy, checks and approvals) never reaches a PR. Only the owner writes it, by hand; rails refuse
  every agent write under the records root ([merge-and-recover](references/merge-and-recover.md#records-are-owner-only)).
- Ask the owner before: force-pushing default, rewriting any other history, deleting branches the agent did not create, using another remote.
- Never commit directly to the default branch.
- Primary remote not GitHub: stop before pushing and ask the owner; this workflow authorizes no other host.

## Git safety

- First run `git status`, `git diff` and `git log`, before edits, staging, commits or pushes.
- Inspect changed files for unrelated work and secrets; never stage secrets.
- Conventional Commits (`feat|fix|refactor|build|ci|chore|docs|style|perf|test`), one atomic commit per logical step.
- Create new logical commits through `git commit`, where rails run the commit checks.
  Agent-rails covers supported shell/eval forms, not arbitrary interpreters or all possible commit creators.
- Never run `git reset --hard`, `git clean`, `git restore` or `rm`.
- Never run repo-wide search/replace scripts (`sed -i`, `perl -pi -e`, `python -c`).
- Use the repository's package manager and runtime.

## Runbooks

- Creating a branch, committing, pushing, opening a PR: [Branch and integrate](references/branch-and-integrate.md)
- Writing or rewriting a PR description: [PR description](references/pr-description.md)
- PR ready, or its head changed: [Verify loop](references/verify-loop.md)
- Merging a PR, or recovering a red default branch: [Merge and recover](references/merge-and-recover.md)
