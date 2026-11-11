# Merge and recover

Main integrates one verified PR at a time. Units target the default resolved in
[branch-and-integrate](branch-and-integrate.md) and use rebase-merge, never squash or merge commits,
unless the repository's project context (`.omp/AGENTS.md`) documents a target/merge-method exception.
Authority and approval boundaries remain in [git-tool](../SKILL.md).

## Merge one pull request

1. Main re-reads `.omp/gate.json`; invalid policy fails closed and `agentMerge: disabled` requires owner approval.
   At this merge boundary, require the latest target branch's exact-SHA CI green under the table below.
   Do not use a default-branch check from the beginning of a long review. The verified recovery revert is the
   exception to the red-target stop, never permission for unrelated integrations.
2. Confirm the actual PR target, current exact head, no unresolved blockers, hook eligibility, required CI success,
   passing current-head risk-tier verification, complete verification comment and a successful `agent-verified`
   commit status on the exact head. [Verify-loop](verify-loop.md) owns evidence, the `agent-verified` status and every-new-SHA invalidation; [patterns](skill://omp-dyna-workflows/references/patterns.md)
   owns docs eligibility and pass markers. With dynaflows on, merge requires the valid exact-head marker and
   `--match-head-commit <40-hex>`. With `ci` declared in `.omp/gate.json` or dynaflows on, rails require
   `--match-head-commit` plus the declared required context and `agent-verified` successful on that SHA. With `ci` declared,
   only that context decides; other reported checks never block. Without it, every reported check must be green.
   Never manufacture markers. Main independently rechecks exact-head CI immediately before merge.
   If the PR adds or changes an owner-approval field, hand it to the owner to merge (see **Owner-approval changes** below).
3. Inspect `mergeStateStatus` and protection's up-to-date requirement. Ordinary PRs rebase only for conflicts (`DIRTY`),
   or `BEHIND` when protection requires up-to-date. Otherwise merge the verified head without rewriting the branch.
   Delegate the rebase/conflict resolution to the assigned writer, including conflict-free rebases; main never authors commits.
   The writer fetches the target, rebases, then pushes self-created `agent/*` history with `git push --force-with-lease`.
   Rewriting other history requires owner approval. All changed-head handling follows verify-loop; pure rebases do not spend a fix round.
4. Merge exactly one ordinary PR with `gh pr merge <number> --rebase --match-head-commit <verified-sha>`;
   add `--auto` when required protection supports it. The evidenced no-CI case or unavailable auto-merge still requires
   all applicable checks/evidence first. Never use `--admin`, bypass protection or queue multiple integrations.
5. After every attempt, including errors or auto-merge requests, independently run
   `gh pr view <number> --json state,mergeCommit`. Require `MERGED` and a non-null merge commit before reporting success.
   An open/queued PR is not merged; inspect state before retrying uncertain errors. If queued head/base changes, reconfirm evidence before it merges.
6. Record the resulting target-branch merge SHA and its CI submission/run/status on the PR; submit target CI with the
   gate's runner role if it does not start on push,
   then continue without waiting here. Before the next integration, require latest-target CI green under the table.
   Report the merged PR/SHA and actual verification outcome. In this step, remove the merged PR's worktree with
   `git worktree remove <absolute path>` and delete its agent-created branch; never delete a branch the agent did not create.

## CI waits, errors and fallbacks

The writer submits/waits for PR-head CI; main independently confirms existing exact-head status before verification and merge.
Main owns latest-target CI immediately before integration and post-merge recording/submission. Every dispatch, status and log refers to
an exact SHA. If a branch moves, its run cannot approve the new SHA. With `ci` declared, the required context decides; otherwise inspect all
reported/required GitHub checks. Inspect protection settings too;
missing protection or a protection API error never waives an existing gate. CI must run `checks.ci`.

| Observed state | Action for PR head | Action for latest target/default |
| --- | --- | --- |
| Success, required statuses published for exact SHA | Main may proceed with other prerequisites | Main may integrate next PR |
| Pending / in progress | Writer waits | Main waits before integration |
| Wait timeout | Repeat the same wait once; then stop/escalate | Same; no integration while unresolved |
| Infrastructure error, unreachable service or publication error | Resubmit once and wait; then stop/escalate | Same; no integration while unresolved |
| CI provider unavailable (jobs never started: zero steps with a billing/quota/account annotation) | Read annotations once (e.g. `gh api repos/<owner>/<repo>/check-runs/<id>/annotations`); never rerun jobs as a probe. Escalate once to the owner, keep PRs open and stop opening further dependent PRs. Report provider-unavailable, not a red change; use a fallback only when the repository documents one (see below) | Same; no integration while unresolved |
| Genuine failed/cancelled CI | Return logs to assigned writer for repair | Stop integrations; preserve logs and use recovery below |
| Missing / unknown required state | Stop and resolve/escalate | Stop integration and resolve/escalate |

Infrastructure failures are not red proof or red CI assertion evidence. Never waive a required gate.

### Declared CI contract

`.omp/gate.json` `ci` declares `requiredContext` (the commit-status context or check-run name that must be `success`
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

Rails read `ci` from the checkout's `.omp/gate.json` and require the declared context `success` on the exact PR head
before the `agent-verified` status may be posted and before `gh pr merge`. Only that context decides: a red or pending
optional check (a third-party app, a docs preview, a non-required workflow) never blocks.
Without `ci` declared, inspect all reported and `--required` checks: for a PR use `gh pr checks <number> --watch` and
validate their head/run SHA against the recorded PR head (watch success alone does not establish SHA identity); for the
target/default use exact-SHA check-run and commit-status queries, not a PR-number watch. Record the exact run IDs/SHA.

### Fallback and no-CI boundary

A repository-specific documented fallback is autonomous merge authority only if its exact `run.headSha` is validated
and it publishes every required status. Otherwise stop/escalate the incident for owner decision; generic Actions success
alone is insufficient. Repository-specific fallbacks live in that repository's project context (`.omp/AGENTS.md`).
Validate a dispatched fallback run's exact head SHA; its success alone does not satisfy a missing declared required context.

Repositories that declare `ci` in `.omp/gate.json` never qualify for the no-CI exception. Otherwise, only when host queries establish
**no checks at all** and the repository has **no CI workflow** may exact-head local `checks.ci` replace CI.
Record commands, outputs, SHA, host-query evidence and `no CI / no protection` in the PR comment.
With dynaflows on, valid schema-v2 commit/pathCheck controls and installed hook remain prerequisites to runtime `prove()`;
this exception cannot evade bootstrap. With dynaflows off only, absent `checks.ci` may use documented test/lint commands;
that fallback cannot manufacture a prove marker. Risk-tier verification still applies. Unknown host state or commands block.

After merge, record/submit target CI and continue; immediately before the next merge wait for the latest target SHA to be green.
Recovery also waits for restored-green target CI after the revert before any further integration.

### Owner-approval changes

Approval = the owner merges the PR that records it. A single owner cannot approve their own PR on GitHub, so the owner's
merge is the only checkable approval act. Guardrail approval fields (`approved_skips`, profile exemption `approval`,
`guardrails.coverage_ratchet.approvals`, rule-coverage `user_approval`, `fast_tier_budget.machines` approvals,
`house_style.exceptions`, and any field named `approval`, `*_approval` or `approved_*`) live in `.omp/gate.json`,
`.omp/test-profile.md` front matter and `.omp/rule-coverage.json`. Rails compare those files at the PR base and exact head
before every agent `gh pr merge`, whether or not the repo declares `ci` or turns dynaflows on, and refuse a merge that
adds or changes an approval record; removals stay mergeable. A PR that rails cannot read also refuses the merge.
On that refusal, do not retry or split the change to evade it: leave the PR open with green CI and `agent-verified`,
report it to the owner as awaiting owner merge, and continue with unrelated work. The field list lives in `approvalFields`
in `agent/extensions/agent-rails.ts`.

## Recover a red default branch

1. Stop new integrations. Preserve failed CI logs and identify the responsible PR/landed range; rebase-merge need not create a merge commit.
2. Delegate a recovery unit to an implementation task on a fresh `agent/revert-<slug>` branch from the fetched current target.
   Revert the landed range with new hook-running commits (use no-commit history operations followed by approved `git commit`);
   never reset/rewrite default. Unsafe attribution or dependent changes require owner escalation with evidence.
3. Open the revert PR before fix-forward work. Use the same hook, manifest, full `prove(..., target_ref=...)`, required CI,
   evidence and `agent-verified` status. Never merge a red revert; `agentMerge: disabled` still applies.
4. Main merges the verified revert serially, then waits for restored-green target CI before any further integration or durable fix.
5. Implement the durable fix as a separate verified PR with regression evidence. Use `skill://codify-omp-workflow-corrections`
   when the incident exposes a repeated class. Owner authority never permits unrelated changes or check bypasses.
