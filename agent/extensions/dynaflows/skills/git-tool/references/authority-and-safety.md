# Authority and safety

This runbook applies in both dynaflows modes. Merge authority, CI and recovery are structured mode only and live
in [merge-and-recover](merge-and-recover.md).

## Record

A repo's **record**, when present, is its personal harness config: `repos/<owner>/<name>/` under the oh-mi-pi-config root,
named by the clone's `origin` remote. It holds `gate.json` (commands, `ci`, `agentMerge`)
and `profile.md`. [The record contract](../../../record/README.md) defines its schema, resolution,
[`ci` and `agentMerge`](../../../record/README.md#merge-and-ci-policy) policy.
A record is not a prerequisite for ordinary Git work; its declared policy still applies.

The assigned writer commits, pushes, writes the description and opens the PR.
In minimal mode the owner merges it; in structured mode main merges it under
[merge authority](merge-and-recover.md#merge-authority).

## Model and owner authority

- Pando model: one unit per `agent/<slug>` branch, one writer per branch, one PR per unit.
  Only same-branch writes contend: independent units run independently.
  - Short-lived `agent/*` branches are the trunk-based unit; target same-day merge. A unit waits only on a unit it builds on.
  - Land multi-phase work incrementally behind a flag with a stated removal criterion; never long-lived or stacked branches.
- Granted in every repository unless its record's `gate.json` has `"agentMerge": "disabled"`: create `agent/*` branches, commit,
  push them to the existing GitHub primary remote and open PRs.
  This grant overrides recommend-before-acting for these actions only.
- Remote/default resolution belongs to [branch-and-integrate](branch-and-integrate.md).
- Pulling existing commits into a branch (merge the target, rebase, cherry-pick, revert, sync) needs
  no extra checks.
- Agents write per-repo records directly under [Who writes records](#who-writes-records).
- Ask the owner before: force-pushing default, rewriting any other history, deleting branches the agent did not create, using another remote.
- Primary remote not GitHub: stop before pushing and ask the owner; this workflow authorizes no other host.

## Who writes records

The repo's record (`<harness>/repos/<owner>/<name>/`: `gate.json`, `profile.md` and anything else there) holds its
checks, profile guardrail settings, exceptions and approvals. It is never committed and never part of a PR.
Agents write and repair a record at any time, in any session; rails never refuse record writes and re-read the record
on every action, so a change takes effect at once. Write approval fields only as the owner stated them in the chat.
Record-write authority does not approve repository fixes that separately require owner approval.

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
