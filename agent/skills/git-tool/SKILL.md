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

The orchestrating (main) agent owns each change from branch to merge; the owner reviews merged PRs afterwards when he chooses.
Implementation tasks commit, push, write the description, open the PR and wait for exact-head green CI before handing off.
Proof tasks leave files uncommitted and never stage, commit, push or open PRs.
Main runs `prove()` (or `exempt_docs_only()`), posts its objective-and-judgement comment (`prove()` posts its own result), ensures the SHA-bound `agent-verified` status and merges; main never commits or writes the PR body.
Read before any Git action. Where this is silent: keep existing content, make no irreversible write,
ask the user once when the choice changes the outcome, state the choice in the result.

## Model and owner authority

- Pando model: one verified unit per `agent/<slug>` branch, one writer per branch, one PR per unit.
  Main verifies, coordinates fixes and merges; implementation tasks commit through the hook and deliver ready PRs. Main never commits.
  Only same-branch writes contend: independent units run in parallel and merge one at a time.
  - Short-lived `agent/*` branches are the trunk-based unit; target same-day merge. A unit waits only on a unit it builds on.
  - Every landed commit stays green: the pre-commit hook runs `checks.commit`; never bypass it.
  - The proof/implementation task sequence and atomic-proof rule belong to
    [dynaflows SKILL stage 4](skill://omp-dyna-workflows/SKILL.md).
  - Land multi-phase work incrementally behind a flag with a stated removal criterion; never long-lived or stacked branches.
- Risk-tier eligibility and helper contracts: [patterns](skill://omp-dyna-workflows/references/patterns.md).
  Runtime-loaded prose is code. Main applies the tier through [verify-loop](references/verify-loop.md).
- Granted in every repository unless `.omp/gate.json` has `"agentMerge": "disabled"`: create `agent/*` branches, commit,
  push them to the existing GitHub primary remote, open PRs, and merge them once required CI (`checks.ci`) and
  cross-family verification pass, subject to the risk tiers. This grant overrides recommend-before-acting for these actions only.
  CI exceptions and incident handling belong to [merge-and-recover](references/merge-and-recover.md#ci-waits-errors-and-fallbacks);
  missing protection alone never waives existing CI.
- Remote/default resolution belongs to [branch-and-integrate](references/branch-and-integrate.md).
  Rebase conditions, force-with-lease and any project-documented merge-method exception belong to
  [merge-and-recover](references/merge-and-recover.md).
- A PR that adds or changes an owner-approval field is the owner's to merge: approval = the owner merges the PR that
  records it ([merge-and-recover](references/merge-and-recover.md#owner-approval-changes)).
- Ask the owner before: force-pushing default, rewriting any other history, deleting branches the agent did not create, using another remote.
- Never commit directly to the default branch.
- Primary remote not GitHub: stop before pushing and ask the owner; this workflow authorizes no other host.

## Git safety

- First run `git status`, `git diff` and `git log`, before edits, staging, commits or pushes.
- Inspect changed files for unrelated work and secrets; never stage secrets.
- Conventional Commits (`feat|fix|refactor|build|ci|chore|docs|style|perf|test`), one atomic commit per logical step.
- Create new logical commits through the approved hook-running `git commit` path, including after history operations.
  Do not assume revert, am, pull, cherry-pick, merge or rebase universally runs the pre-commit hook.
  Agent-rails covers supported shell/eval forms, not arbitrary interpreters or all possible commit creators.
- Never run `git reset --hard`, `git clean`, `git restore` or `rm`.
- Never run repo-wide search/replace scripts (`sed -i`, `perl -pi -e`, `python -c`).
- Use the repository's package manager and runtime.

## Runbooks

- Creating a branch, committing, pushing, opening a PR: [Branch and integrate](references/branch-and-integrate.md)
- Writing or rewriting a PR description: [PR description](references/pr-description.md)
- PR code-ready, or a verification round requests changes: [Verify loop](references/verify-loop.md)
- Merging a verified PR, or recovering a red default branch: [Merge and recover](references/merge-and-recover.md)
