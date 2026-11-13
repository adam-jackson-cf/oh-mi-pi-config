# Merge and recover

Units target the default resolved in [branch-and-integrate](branch-and-integrate.md) and use rebase-merge,
never squash or merge commits, unless the repository's project instructions document a target/merge-method exception.
Authority and approval boundaries remain in [git-tool](../SKILL.md).

## Merge one pull request

1. Main re-reads the repo's record `gate.json`; invalid policy fails closed and `agentMerge: disabled` requires owner approval.
2. Confirm the actual PR target, the current exact head and required CI green on it under the table below.
   With dynaflows on, also confirm `prove()`'s result file for that exact head has `status: pass`
   ([verify-loop](verify-loop.md)). Rails' `gh pr merge` guard (dynaflows on) checks the same two things.
3. Inspect `mergeStateStatus` and protection's up-to-date requirement. Rebase only for conflicts (`DIRTY`),
   or `BEHIND` when protection requires up-to-date. Delegate the rebase to the assigned writer; main never authors commits.
   The writer fetches the target, rebases, then pushes self-created `agent/*` history with `git push --force-with-lease`.
   Rewriting other history requires owner approval. A rebased head needs exact-head CI and, with dynaflows on, one
   `prove()` run on it; nothing else.
4. Merge with `gh pr merge <number> --rebase --match-head-commit <head-sha>`; add `--auto` when required protection
   supports it. Never use `--admin` or bypass protection.
5. After every attempt, including errors or auto-merge requests, run `gh pr view <number> --json state,mergeCommit`.
   Require `MERGED` and a non-null merge commit before reporting success. An open/queued PR is not merged; inspect state
   before retrying uncertain errors.
6. Report the merged PR/SHA. Remove the merged PR's worktree with `git worktree remove <absolute path>` and delete its
   agent-created branch; never delete a branch the agent did not create.

## CI waits, errors and fallbacks

The writer submits and waits for PR-head CI. Every dispatch, status and log refers to an exact SHA; a run for an old
head cannot approve a new one. With `ci` declared, the required context decides; otherwise inspect all reported/required
GitHub checks. Missing protection or a protection API error never waives an existing gate. CI must run `checks.ci`.

| Observed state on the PR head | Action |
| --- | --- |
| Success, required statuses published for exact SHA | Proceed |
| Pending / in progress | Writer waits |
| Wait timeout | Repeat the same wait once; then stop/escalate |
| Infrastructure error, unreachable service or publication error | Resubmit once and wait; then stop/escalate |
| CI provider unavailable (jobs never started: zero steps with a billing/quota/account annotation) | Read annotations once (e.g. `gh api repos/<owner>/<repo>/check-runs/<id>/annotations`); never rerun jobs as a probe. Escalate once to the owner, keep PRs open and stop opening further dependent PRs. Use a fallback only when the repository documents one (see below) |
| Genuine failed/cancelled CI | Return logs to the assigned writer for repair |
| Missing / unknown required state | Stop and resolve/escalate |

Infrastructure failures are not red proof or red CI assertion evidence. Never waive a required gate.

### Declared CI contract

The record's `gate.json` `ci` declares `requiredContext` (the commit-status context or check-run name that must be `success`
on the exact head, whoever posts it) and `runner` (a role name). The machine config `ci-runners.json` in the omp base
directory maps each role to its submit, wait, status and logs commands; `scripts/device-rollout.py` writes it per device.
Drive CI through `scripts/ci-runner.py` in the omp base directory with the gate's runner role, the repository and the exact 40-hex SHA:

```sh
python3 <omp base>/scripts/ci-runner.py submit <runner> OWNER/REPO SHA
python3 <omp base>/scripts/ci-runner.py wait <runner> OWNER/REPO SHA --context <requiredContext> [--timeout S]
python3 <omp base>/scripts/ci-runner.py status <runner> OWNER/REPO SHA --context <requiredContext>
python3 <omp base>/scripts/ci-runner.py logs <runner> OWNER/REPO SHA [--tail N]
```

Wait defaults to the role's configured `timeout`; `--timeout` overrides it. A role whose runner starts on push reports that submit is not needed. A role without its own wait/status commands uses
the built-in GitHub poller over the exact SHA's commit statuses and check runs; wait exits 0 on success, 124 on timeout
and non-zero on failure. A missing role means the device's machine config is not set up: run `scripts/device-rollout.py`;
that is a setup gap, never a CI waiver. Exit codes alone can be ambiguous: inspect status and logs before choosing the table action.
Require successful publication of the required context on GitHub for the exact head, not just a local successful result.

Only the declared context decides: a red or pending optional check (a third-party app, a docs preview, a non-required
workflow) never blocks. Without `ci` declared, inspect all reported and `--required` checks: for a PR use
`gh pr checks <number> --watch` and validate their head/run SHA against the recorded PR head (watch success alone does not
establish SHA identity).

### Fallback and no-CI boundary

A repository-specific documented fallback is autonomous merge authority only if its exact `run.headSha` is validated
and it publishes every required status. Otherwise stop/escalate the incident for owner decision; generic Actions success
alone is insufficient. Repository-specific fallbacks live in that repository's project instructions.

Repositories whose record declares `ci` never qualify for the no-CI exception. Otherwise, only when host queries establish
**no checks at all** and the repository has **no CI workflow** may exact-head local `checks.ci` replace CI.
Record commands, outputs, SHA, host-query evidence and `no CI / no protection` in a PR comment.
With dynaflows on, a record with valid schema-v2 controls remains a prerequisite to `prove()`; this exception cannot
evade bootstrap. Unknown host state or commands block.

### Records are owner-only

The repo's record (`<harness>/repos/<owner>/<name>/`: `gate.json`, `profile.md` and anything else there) holds its
checks, `pathCheck`, `guardrails.check` settings, the `dynaflows` switch, exceptions and approvals. It is never
committed and never part of a PR, so the owner's hand edit is its only review. Rails refuse every agent edit-tool
write to a path under the records root (a lexical check on the resolved path). On that refusal, do not retry through
another tool or split the change to evade it: print the drafted change (or write it to a draft outside `repos/`), ask
the owner to save it by hand, and continue with unrelated work.

## Recover a red default branch

1. Stop opening dependent PRs. Preserve failed CI logs and identify the responsible PR/landed range.
2. Delegate a revert to an implementation task on a fresh `agent/revert-<slug>` branch from the fetched current target.
   Revert the landed range; never reset or rewrite default. Unsafe attribution or dependent changes require owner
   escalation with evidence.
3. The revert is an ordinary PR with no behaviour proofs: exact-head CI green and, with dynaflows on, `prove()` on an
   empty manifest. `agentMerge: disabled` still applies. `prove()` blocks a revert that changes a feature-map doc,
   because that doc links no frozen proof; escalate that revert to the owner with the blocking doc paths.
4. Implement the durable fix as a separate PR whose proofs reproduce the failure. Use `skill://codify-omp-workflow-corrections`
   when the incident exposes a repeated class. Owner authority never permits unrelated changes or check bypasses.
