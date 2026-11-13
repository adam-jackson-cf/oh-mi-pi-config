# Dry-run impact

## Objective

Show the full cost of clearing every harness finding before changing the target.

## Required actions

1. Render every candidate repo file under the temporary candidate root and compare it with its
   target counterpart using Git diff or equivalent exact file comparison. Count creates, edits,
   deletes, added/deleted lines, and files with unresolved merge conflicts.
2. Run the harness checks from the harness against a temporary checkout with the draft record:
   `uv run --project <omp root>/agent/skills/setup-repository-guardrails/assets/python-reference
   omp-guardrails check --repo <temporary checkout> --record <draft record dir>`.
   They read nothing from, and write nothing into, the checkout's tree. Count findings by rule ID
   and path over the whole repository. Steps the draft record's `guardrails.check.repo` gives
   to repo checkers run through `checks.commit` instead; report that command's result with
   them. Mark an estimate `unknown` where safe execution or a
   candidate checker is unavailable.
3. Report one view: the whole repository minus the draft record's scope exclusions. Adopting a
   repository means clearing every finding first; there is no baseline and no new/changed-code
   view. A narrower rollout changes which rules are enabled, never which paths a rule scans.
4. Estimate setup dependencies and added local/CI runtime from measured commands when possible.
   Label measured values separately from estimates and state their cache/platform assumptions.
5. Report missing implementations, unresolved existing-rule conflicts, and the work needed to
   reach zero findings. Include the full-plan total even if a narrower rollout is recommended.
6. Keep candidate files, the draft record and command output temporary and outside protected
   paths. Do not run fix mode, install packages, stage files, or write a retained summary into
   the target during dry run.

## Done when

- Whole-repo findings, file/line estimates, confidence and unknowns are visible.
- The owner can compare the Minimal and Full rule sets without hidden rule omissions.
