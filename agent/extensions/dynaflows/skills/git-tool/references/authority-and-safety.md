# Authority and safety

This runbook applies in both dynaflows modes. Merge authority, CI and recovery are structured mode only and live
in [merge-and-recover](merge-and-recover.md).

## Repo config

A repo's **config**, when present, is `.agentrails/config.json` in its main checkout, kept out of git and shared by
its worktrees. [The config contract](../../../CONFIG.md) defines its sections, writers and the
[`autonomy`](../../../CONFIG.md#autonomy) and [`ci`](../../../CONFIG.md#ci) policy.
A config is not a prerequisite for ordinary Git work; its declared policy still applies.

The assigned writer commits, pushes, writes the description and opens the PR.
In minimal mode the owner merges it; in structured mode main merges it under
[merge authority](merge-and-recover.md#merge-authority).

## Model and owner authority

- Pando model: one unit per `agent/<slug>` branch, one writer per branch, one PR per unit.
  Only same-branch writes contend: independent units run independently.
  - Short-lived `agent/*` branches are the trunk-based unit; target same-day merge. A unit waits only on a unit it builds on.
  - Land multi-phase work incrementally behind a flag with a stated removal criterion; never long-lived or stacked branches.
- Granted in every repository, subject to its config's `autonomy`: create `agent/*` branches, commit,
  push them to the existing GitHub primary remote and open PRs. An `ask` action waits for the owner's
  `$approve autonomy:<action>:<subject>` line.
  This grant overrides recommend-before-acting for these actions only.
- Remote/default resolution belongs to [branch-and-integrate](branch-and-integrate.md).
- Pulling existing commits into a branch (merge the target, rebase, cherry-pick, revert, sync) needs
  no extra checks.
- Agents never write the repo config; see [Who writes the config](#who-writes-the-config).
- Ask the owner before: force-pushing default, rewriting any other history, deleting branches the agent did not create, using another remote.
- Primary remote not GitHub: stop before pushing and ask the owner; this workflow authorizes no other host.

## Who writes the config

The repo config holds its commands, checker settings, test profile, exceptions and approvals. It is never committed and
never part of a PR. Agent tools never write it: rails refuse every edit, write, eval or shell write to a `.agentrails`
path, and re-read the config on every action. Each section has one writer, listed in the
[config contract](../../../CONFIG.md#sections-and-writers); exceptions and approvals come only from the owner's
`$approve` and `$revoke` lines.

## Git safety

- First run `git status`, `git diff` and `git log`, before edits, staging, commits or pushes.
- Inspect changed files for unrelated work and secrets; never stage secrets.
- Never commit or push directly to the default branch.
- Conventional Commits (`feat|fix|refactor|build|ci|chore|docs|style|perf|test`), one atomic commit per logical step.
- Create new logical commits through `git commit`, where rails run the commit checks.
- Never bypass configured harness commit checks or the repo's own hooks.
- Never run `git reset --hard`, `git clean`, `git restore` or `rm`.
- Never run repo-wide search/replace scripts (`sed -i`, `perl -pi -e`, `python -c`).
- Use the repository's package manager and runtime.
