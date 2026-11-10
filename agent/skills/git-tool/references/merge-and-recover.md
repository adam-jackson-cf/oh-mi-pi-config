# Merge and recover

Main integrates one verified PR at a time. Ordinary units target the default resolved in
[branch-and-integrate](branch-and-integrate.md) and use rebase-merge, never squash or merge commits;
the [nuc-config sync exception](#nuc-config-sync-exception) below explicitly requires a merge commit.
Authority and approval boundaries remain in [git-tool](../SKILL.md).

## Merge one pull request

1. Main re-reads `.omp/gate.json`; invalid policy fails closed and `agentMerge: disabled` requires owner approval.
   At this merge boundary, require the latest target branch's exact-SHA CI green under the table below.
   Do not use a default-branch check from the beginning of a long review. The verified recovery revert is the
   exception to the red-target stop, never permission for unrelated integrations.
2. Confirm the actual PR target, current exact head, no unresolved blockers, hook eligibility, required CI success,
   passing current-head risk-tier verification, complete verification comment and `agent-verified`.
   [Verify-loop](verify-loop.md) owns evidence/labels and every-new-SHA invalidation; [patterns](skill://omp-dyna-workflows/references/patterns.md)
   owns docs eligibility and pass markers. With dynaflows on, merge requires the valid exact-head marker and
   `--match-head-commit <40-hex>`; rails also check that the PR head matches and its checks are green at `gh pr merge`.
   Never manufacture markers. Main independently rechecks exact-head CI immediately before merge.
3. Inspect `mergeStateStatus` and protection's up-to-date requirement. Ordinary PRs rebase only for conflicts (`DIRTY`),
   or `BEHIND` when protection requires up-to-date. Otherwise merge the verified head without rewriting the branch.
   Delegate the rebase/conflict resolution to the assigned writer, including conflict-free rebases; main never authors commits.
   The writer fetches the target, rebases, then pushes self-created `agent/*` history with `git push --force-with-lease`.
   Rewriting other history requires owner approval. All changed-head handling follows verify-loop; pure rebases do not spend a fix round.
   NUC sync incorporates target updates with hook-owned merge commits, not rebases of main history.
4. Merge exactly one ordinary PR with `gh pr merge <number> --rebase --match-head-commit <verified-sha>`;
   add `--auto` when required protection supports it. The evidenced no-CI case or unavailable auto-merge still requires
   all applicable checks/evidence first. Never use `--admin`, bypass protection or queue multiple integrations.
5. After every attempt, including errors or auto-merge requests, independently run
   `gh pr view <number> --json state,mergeCommit`. Require `MERGED` and a non-null merge commit before reporting success.
   An open/queued PR is not merged; inspect state before retrying uncertain errors. If queued head/base changes, reconfirm evidence before it merges.
6. Record the resulting target-branch merge SHA and its CI submission/run/status on the PR; submit NUC CI if needed,
   then continue without waiting here. Before the next integration, require latest-target CI green under the table.
   Report the merged PR/SHA and actual verification outcome. In this step, remove the merged PR's worktree with
   `git worktree remove <absolute path>` and delete its agent-created branch; never delete a branch the agent did not create.

## CI waits, errors and fallbacks

The writer submits/waits for PR-head CI; main independently confirms existing exact-head status before verification and merge.
Main owns latest-target CI immediately before integration and post-merge recording/submission. Every dispatch, status and log refers to
an exact SHA. If a branch moves, its run cannot approve the new SHA. Inspect all reported/required GitHub checks and protection settings;
missing protection or a protection API error never waives an existing gate. CI must run `checks.ci`.

| Observed state | Action for PR head | Action for latest target/default |
| --- | --- | --- |
| Success, required statuses published for exact SHA | Main may proceed with other prerequisites | Main may integrate next PR |
| Pending / in progress | Writer waits | Main waits before integration |
| Wait timeout | Repeat the same wait once; then stop/escalate | Same; no integration while unresolved |
| Infrastructure error, unreachable service or publication error | Resubmit once and wait; then stop/escalate | Same; no integration while unresolved |
| CI provider unavailable (jobs never started: zero steps with a billing/quota/account annotation) | Read annotations once (e.g. `gh api repos/<owner>/<repo>/check-runs/<id>/annotations`); never rerun jobs as a probe. Escalate once to the owner, keep PRs open and stop opening further dependent PRs. Report provider-unavailable, not a red change; NUC fallback only when the repo is in `scripts/ci-gate-repos.json` | Same; no integration while unresolved |
| Genuine failed/cancelled CI | Return logs to assigned writer for repair | Stop integrations; preserve logs and use recovery below |
| Missing / unknown required state | Stop and resolve/escalate | Stop integration and resolve/escalate |

Infrastructure failures are not red proof or red CI assertion evidence. Never waive a required gate.

### GitHub-hosted CI

For a PR use `gh pr checks <number> --watch`, also inspecting all and `--required` checks. Validate their associated
head/run SHA against the recorded PR head; watch success alone does not establish SHA identity.
For the target/default use exact-SHA workflow/check-run and commit-status queries (for example `gh run list --commit <sha>`
and `gh run view <run-id> --json headSha,status,conclusion` plus required commit/check statuses), not a PR-number watch.
Record the exact run IDs/SHA. Apply the table to both hosts.

### NUC-hosted CI

For `.omp/gate.json` `ciHost: nuc`, use these commands with the repository and exact 40-hex SHA:

```sh
ssh -o BatchMode=yes codex-omarchy-nuc 'bash -lc "python3 ~/.omp/scripts/ci-gate.py submit OWNER/REPO SHA"'
ssh -o BatchMode=yes codex-omarchy-nuc 'bash -lc "python3 ~/.omp/scripts/ci-gate.py wait OWNER/REPO SHA --timeout 900"'
ssh -o BatchMode=yes codex-omarchy-nuc 'bash -lc "python3 ~/.omp/scripts/ci-gate.py status OWNER/REPO SHA"'
ssh -o BatchMode=yes codex-omarchy-nuc 'bash -lc "python3 ~/.omp/scripts/ci-gate.py logs OWNER/REPO SHA --tail 100"'
```

NUC executes `checks.ci` and publishes required GitHub commit status `gate` on the exact head. Rails read `ciHost`
from the checkout's `.omp/gate.json` and require this successful status for `agent-verified` labels and, with dynaflows on,
merges; a check run named `gate` never counts. Submit returns immediately; cached success/failure is reused and
infrastructure `error` retries on resubmission. After a publication error, resubmitting republishes the stored result;
resubmit once and wait, then stop/escalate under the table. Wait exits 0 on success, 2 on timeout, and 1 on failure,
error or publication failure. Exit 1 alone is ambiguous: inspect status/logs, result state and `publicationError` before
choosing the table action. Require successful publication of every required status, not just a local successful result.
A missing entry in oh-mi-pi-config's `scripts/ci-gate-repos.json` is a setup gap requiring its own PR, never a CI waiver.

### Fallback and no-CI boundary

A repository-specific documented fallback is autonomous merge authority only if its exact `run.headSha` is validated
and it publishes every required status. Otherwise stop/escalate the incident for owner decision; generic Actions success
alone is insufficient. For oh-mi-pi-config see its [CI gate README (NUC host)](https://github.com/adam-jackson-cf/oh-mi-pi-config#ci-gate-nuc-host)
and actual [ci-quality-gates.yml](https://github.com/adam-jackson-cf/oh-mi-pi-config/blob/main/.github/workflows/ci-quality-gates.yml):
`gh workflow run ci-quality-gates.yml --repo adam-jackson-cf/oh-mi-pi-config --ref <branch>`.
Validate the dispatched run's exact head SHA. Its passing Actions run alone does not satisfy a missing NUC `gate` status.

Repositories with `ciHost: nuc` never qualify for the no-CI exception. Otherwise, only when host queries establish
**no checks at all** and the repository has **no CI workflow** may exact-head local `checks.ci` replace CI.
Record commands, outputs, SHA, host-query evidence and `no CI / no protection` in the PR comment.
With dynaflows on, valid schema-v2 commit/pathCheck controls and installed hook remain prerequisites to runtime `prove()`;
this exception cannot evade bootstrap. With dynaflows off only, absent `checks.ci` may use documented test/lint commands;
that fallback cannot manufacture a prove marker. Risk-tier verification still applies. Unknown host state or commands block.

After merge, record/submit target CI and continue; immediately before the next merge wait for the latest target SHA to be green.
Recovery also waits for restored-green target CI after the revert before any further integration.

## nuc-config sync exception

`agent/nuc-sync-<date>` incorporates main into nuc-config, preserving main ancestry and Linux-specific settings.
Main fetches both refs; the writer creates the sync branch from fetched nuc-config and incorporates fetched main using
`git merge --no-commit --no-ff <remote>/main`, resolving settings deliberately, then the approved hook-running `git commit`.
Do not let a history operation silently create an unhooked logical commit. Target this PR at nuc-config, not default.
Use its fetched `<remote>/nuc-config` as `target_ref` and its merge-base with head as `parent` in main's full runtime `prove()`.
Sync is not docs-exempt merely because it adds no new product code.

All exact-head CI, verification comment, `agent-verified`, marker, serial integration and no-bypass requirements above remain.
Main uses `gh pr merge <number> --merge --match-head-commit <verified-sha>` (optionally `--auto` with required checks),
never squash or rebase-merge for this sync. Confirm `MERGED`, fetch the landed nuc-config ref, and confirm the incorporated
main SHA is its ancestor (`git merge-base --is-ancestor <incorporated-main-sha> <remote>/nuc-config`) before rollout.
Record/submit nuc-config target CI; wait for its latest SHA before another integration into that target under the same table.

## Recover a red default branch

1. Stop new integrations. Preserve failed CI logs and identify the responsible PR/landed range; rebase-merge need not create a merge commit.
2. Delegate a recovery unit to an implementation task on a fresh `agent/revert-<slug>` branch from the fetched current target.
   Revert the landed range with new hook-running commits (use no-commit history operations followed by approved `git commit`);
   never reset/rewrite default. Unsafe attribution or dependent changes require owner escalation with evidence.
3. Open the revert PR before fix-forward work. Use the same hook, manifest, full `prove(..., target_ref=...)`, required CI,
   evidence and label. Never merge a red revert; `agentMerge: disabled` still applies.
4. Main merges the verified revert serially, then waits for restored-green target CI before any further integration or durable fix.
5. Implement the durable fix as a separate verified PR with regression evidence. Use `skill://codify-omp-workflow-corrections`
   when the incident exposes a repeated class. Owner authority never permits unrelated changes or check bypasses.
