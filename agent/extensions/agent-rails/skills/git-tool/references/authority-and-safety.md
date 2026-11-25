# Authority and safety

This runbook applies to every Git action. Merge authority, CI and recovery live in
[merge-and-recover](merge-and-recover.md).

## Repo config

A repo's **config**, when present, is `.agentrails/config.json` in its main checkout, kept out of git and shared by
its worktrees. [The config contract](../../../docs/config.md) defines its sections, writers and the
[`autonomy`](../../../docs/config.md#autonomy) and [`ci`](../../../docs/config.md#ci) policy.
In a repository with a GitHub origin and no valid config, the rails refuse commits, pushes, PRs and merges
until the owner runs `/agentrails onboard`; while it onboards, the owner merges the bootstrap PR.

The assigned writer commits, pushes, writes the description and opens the PR. Main merges it under
[merge authority](merge-and-recover.md#merge-authority).

## Model and owner authority

- Pando model: one unit per `agent/<slug>` branch, one writer per branch, one PR per unit.
  Only same-branch writes contend: independent units run independently.
  - Short-lived `agent/*` branches are the trunk-based unit; target same-day merge. A unit waits only on a unit it builds on.
  - Land multi-phase work incrementally behind a flag with a stated removal criterion; never long-lived or stacked branches.
- Granted in every governed repository, subject to its config's `autonomy`: create `agent/*` branches,
  commit, push them to the existing GitHub primary remote and open PRs. An `ask` action waits for the owner's
  `$approve autonomy:<action>:<subject>` line.
  This grant overrides recommend-before-acting for these actions only.
- Remote/default resolution belongs to [branch-and-integrate](branch-and-integrate.md).
- Pulling existing commits into a branch (merge the target, rebase, cherry-pick, revert, sync) needs
  no extra checks.
- Agents never write the repo config; see [Who writes the config](#who-writes-the-config).
- Ask the owner before: force-pushing default, rewriting any other history, deleting branches the agent did not create, using another remote.
- Primary remote not GitHub: stop before pushing and ask the owner; this workflow authorizes no other host.

## Who writes the config

The repo config holds its policy, commands, checker settings, test settings, exceptions and approvals. It is never
committed and never part of a PR. Agents never edit it: rails refuse every edit, write, eval or shell write to a
`.agentrails` path, and re-read the config on every action. Each section has one writer
([config contract](../../../docs/config.md#sections-and-writers)):

| Sections | Writer |
| --- | --- |
| `repo`, `onboarding` | The owner's `/agentrails onboard` |
| `autonomy`, `ci`, `gates`, `judgments`, `mergePolicies`, `afterMerge` | The owner's Q&A (`onboard` or `configure`) |
| `commands`, `checkers`, `structure`, `houseStyle`, `tests` | The `agentrails_config` tool after its skill; owner-confirmed once onboarded |
| `exceptions`, `approvals` | The owner's `$approve` and `$revoke` lines only |
| The whole file | The owner's `/agentrails import` |

To change a section, ask the owner for its writer.

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
