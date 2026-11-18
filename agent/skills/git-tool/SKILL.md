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

A repo's **record**, when present, is its personal harness config: `repos/<owner>/<name>/` under the oh-mi-pi-config root,
named by the clone's `origin` remote, gitignored and never committed. It holds `gate.json` (commands, `ci`, `agentMerge`)
and `profile.md`. [The record contract](../../lib/record/README.md) defines its schema, resolution,
[`ci` and `agentMerge`](../../lib/record/README.md#merge-and-ci-policy) policy.
A record is not a prerequisite for ordinary Git work; its declared policy still applies.

The orchestrating (main) agent owns each change from branch to merge; the owner reviews merged PRs afterwards when he chooses.
The assigned writer commits, pushes, writes the description, opens the PR and waits for exact-head green CI before handing off.
Read before any Git action. Where this is silent: keep existing content, make no irreversible write,
ask the user once when the choice changes the outcome, state the choice in the result.

## Model and owner authority

- Pando model: one unit per `agent/<slug>` branch, one writer per branch, one PR per unit.
  Main checks exact-head CI, coordinates fixes and merges; the assigned writer delivers ready PRs.
  Only same-branch writes contend: independent units run and merge independently.
  - Short-lived `agent/*` branches are the trunk-based unit; target same-day merge. A unit waits only on a unit it builds on.
  - Never bypass configured harness commit checks or the repo's own hooks.
  - Land multi-phase work incrementally behind a flag with a stated removal criterion; never long-lived or stacked branches.
- Granted in every repository unless its record's `gate.json` has `"agentMerge": "disabled"`: create `agent/*` branches, commit,
  push them to the existing GitHub primary remote, open PRs, and merge them once required CI is green on the exact head.
  This grant overrides recommend-before-acting for these actions only.
  CI exceptions and incident handling belong to [merge-and-recover](references/merge-and-recover.md#ci-waits-errors-and-fallbacks);
  missing protection alone never waives existing CI.
- Remote/default resolution belongs to [branch-and-integrate](references/branch-and-integrate.md).
  Rebase conditions, force-with-lease and any project-documented merge-method exception belong to
  [merge-and-recover](references/merge-and-recover.md).
- Pulling existing commits into a branch (merge the target, rebase, cherry-pick, revert, sync) needs
  no extra checks: those commits were checked when they were made.
- Agents write per-repo records directly; records are never committed and never part of a PR.
  Write approval fields only as the owner stated them in the chat; `ownerOnlyPaths` remains unchanged
  ([merge-and-recover](references/merge-and-recover.md#who-writes-records)).
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
- PR ready, or its head changed: [CI waits](references/merge-and-recover.md#ci-waits-errors-and-fallbacks)
- Merging a PR, or recovering a red default branch: [Merge and recover](references/merge-and-recover.md)
