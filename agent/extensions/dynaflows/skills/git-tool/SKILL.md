---
name: "git-tool"
description: "Use when the task requires any Git action, from branching and committing
  to opening, describing or merging a pull request or recovering a red default branch."
hide: true
disable-model-invocation: true
---

# Task

Every section applies in both dynaflows modes unless it is marked **Structured mode only**.
The mode is the repo's dynaflows `mode`, `minimal` or `structured`. In minimal mode rails refuse
`gh pr merge` and pushes to the default branch: the agent opens the PR and the owner merges it.

## Authority and safety

- **Purpose**: Apply the repo record, the owner's grant and the Git safety rules that bind every Git action.
- **When**: Read before any Git action.
- **STOP**: Gates owner approval before history rewrites, deleting branches the agent did not create, another remote or a non-GitHub push.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Run `git status`, `git diff` and `git log` first; never `git reset --hard`, `git clean`, `git restore`, `rm` or repo-wide rewrite scripts.
- Runbook: [Authority and safety](references/authority-and-safety.md)

## Branch and integrate

- **Purpose**: Prepare one `agent/<slug>` unit, then commit, push, describe and open its PR.
- **When**: Creating a branch, committing, pushing or opening a PR.
- Runbook: [Branch and integrate](references/branch-and-integrate.md)

## Describe the pull request

- **Purpose**: Write a PR body that directs a cold reviewer to the riskiest changes.
- **When**: Writing or rewriting a PR description.
- Runbook: [PR description](references/pr-description.md)

## Wait for CI

- **Structured mode only.**
- **Purpose**: Get required CI green on the exact PR head, or escalate the incident.
- **When**: A PR is ready, or its head changed.
- Runbook: [CI waits](references/merge-and-recover.md#ci-waits-errors-and-fallbacks)

## Merge and recover

- **Structured mode only.**
- **Purpose**: Merge a ready PR on its exact head, or revert a red default branch.
- **When**: Merging a PR, or recovering a red default branch.
- Runbook: [Merge and recover](references/merge-and-recover.md)
