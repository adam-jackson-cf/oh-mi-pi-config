# Merge and recover

**Structured mode only.** Read this runbook only when the repo's dynaflows `mode` is `structured`.

Units target the default resolved in [branch-and-integrate](branch-and-integrate.md) and use rebase-merge,
never squash or merge commits, unless the config's `mergePolicies` entry for the base and head prefix names another
method; the merge rail enforces it.
Authority and approval boundaries remain in [authority-and-safety](authority-and-safety.md).

## Merge authority

- The orchestrating (main) agent owns each change from branch to merge: it checks exact-head CI, coordinates fixes
  and merges; independent units merge independently. The assigned writer waits for exact-head green CI before handing off.
- Granted in every repository, subject to its config's `autonomy.merge`: merge `agent/*` PRs
  once required CI is green on the exact head. This grant overrides recommend-before-acting for merging only.
- `autonomy.merge: ask` waits for the owner's `$approve autonomy:merge:<pr number>` line; it never permits skipping CI.

## Merge one pull request

1. Main re-reads the repo's config when present; invalid declared policy fails closed.
2. Confirm the actual PR target, the current exact head and required CI green on it under the table below.
3. Inspect `mergeStateStatus` and protection's up-to-date requirement. Rebase only for conflicts (`DIRTY`),
   or `BEHIND` when protection requires up-to-date. The assigned writer fetches the target, rebases,
   then pushes self-created `agent/*` history with `git push --force-with-lease`.
   Rewriting other history requires owner approval. A rebased head needs exact-head CI again.
4. Merge with `gh pr merge <number> --rebase --match-head-commit <head-sha>`, or the method the config's
   `mergePolicies` entry for the PR's base and head names; add `--auto` when required protection supports it. Rails
   refuse `--admin` and any other method.
5. After every attempt, including errors or auto-merge requests, run `gh pr view <number> --json state,mergeCommit`.
   Require `MERGED` and a non-null merge commit before reporting success. An open/queued PR is not merged; inspect state
   before retrying uncertain errors.
6. Report the merged PR/SHA. Remove the merged PR's worktree with `git worktree remove <absolute path>` and delete its
   agent-created branch; never delete a branch the agent did not create.

## CI waits, errors and fallbacks

The writer submits and waits for PR-head CI. Every dispatch, status and log refers to an exact SHA; a run for an old
head cannot approve a new one. With `ci` declared, the required context decides; otherwise inspect all reported/required
GitHub checks. Missing protection or a protection API error never waives an existing gate. Use `commands.ci` when declared.

| Observed state on the PR head | Action |
| --- | --- |
| Success, required statuses published for exact SHA | Proceed |
| Pending / in progress | Writer waits |
| Wait timeout | Repeat the same wait once; then stop/escalate |
| Infrastructure error, unreachable service or publication error | Resubmit once and wait; then stop/escalate |
| CI provider unavailable (jobs never started: zero steps with a billing/quota/account annotation) | Follow the provider-unavailable rule below |
| Genuine failed/cancelled CI | Return logs to the assigned writer for repair |
| Missing / unknown required state | Stop and resolve/escalate |

Provider unavailable: read annotations once (e.g. `gh api repos/{owner}/{repo}/check-runs/<id>/annotations`); never rerun jobs as a probe.
Escalate once to the owner, keep PRs open and stop opening further dependent PRs.
Use a fallback only when the repository documents one (see [Fallback and no-CI boundary](#fallback-and-no-ci-boundary)).

Infrastructure failures are not failed CI assertion evidence. Never waive a required gate.

### Declared CI contract

The config's [`ci` policy](../../../CONFIG.md#ci) declares
`requiredContext` (the commit-status context or check-run name that must be `success`
on the exact head, whoever posts it) and `runner` (a role name). The machine config `ci-runners.json` in the omp base
directory maps each role to its submit, wait, status and logs commands; `scripts/device_rollout.py` writes it per device.
Drive CI through `scripts/ci_runner.py` in the omp base directory with the gate's runner role, the repository and the exact 40-hex SHA:

```sh
python3 <omp base>/scripts/ci_runner.py submit <runner> OWNER/REPO SHA
python3 <omp base>/scripts/ci_runner.py wait <runner> OWNER/REPO SHA --context <requiredContext> [--timeout S]
python3 <omp base>/scripts/ci_runner.py status <runner> OWNER/REPO SHA --context <requiredContext>
python3 <omp base>/scripts/ci_runner.py logs <runner> OWNER/REPO SHA [--tail N]
```

Wait defaults to the role's configured `timeout`; `--timeout` overrides it.
A role whose runner starts on push reports that submit is not needed. A role without its own wait/status commands uses
the built-in GitHub poller over the exact SHA's commit statuses and check runs; wait exits 0 on success, 124 on timeout
and non-zero on failure. A missing role means the device's machine config is not set up: run `scripts/device_rollout.py`;
that is a setup gap, never a CI waiver. Exit codes alone can be ambiguous: inspect status and logs before choosing the table action.
Require successful publication of the required context on GitHub for the exact head, not just a local successful result.

Only the declared context decides: a red or pending optional check (a third-party app, a docs preview, a non-required
workflow) never blocks. Without `ci` declared, inspect all reported and `--required` checks: for a PR use
`gh pr checks <number> --watch` and validate their head/run SHA against the recorded PR head (watch success alone does not
establish SHA identity).

### Fallback and no-CI boundary

A repository-specific documented fallback is autonomous merge authority only if its exact `run.headSha` is validated
and it publishes every required status. Otherwise stop/escalate the incident for owner decision; generic Actions success
alone is insufficient. Repository-specific fallbacks live in the [config contract](../../../CONFIG.md#oh-mi-pi-config-example).

Repositories whose config enables `ci` never qualify for the no-CI exception. Otherwise, only when host queries establish
**no checks at all** and the repository has **no CI workflow** may exact-head local `commands.ci` replace CI.
Record commands, outputs, SHA, host-query evidence and `no CI / no protection` in a PR comment.
Without a declared local CI command, or with unknown host state, stop and resolve/escalate.

## Recover a red default branch

1. Stop opening dependent PRs. Preserve failed CI logs and identify the responsible PR/landed range.
2. Delegate a revert to an implementation task on a fresh `agent/revert-<slug>` branch from the fetched current target.
   Revert the landed range; never reset or rewrite default. Unsafe attribution or dependent changes require owner
   escalation with evidence.
3. The revert is an ordinary PR requiring exact-head CI green. `autonomy.merge: ask` still applies.
4. Implement the durable fix as a separate PR with regression coverage. Owner authority never permits unrelated
   changes or check bypasses.
