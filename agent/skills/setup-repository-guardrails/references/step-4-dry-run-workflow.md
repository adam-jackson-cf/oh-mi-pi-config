# Dry-run impact

## Objective

Show the full cost of enforcing all applicable rules before changing the target.

## Required actions

1. Render every candidate file under the temporary candidate root and compare it with its target
   counterpart using Git diff or equivalent exact file comparison. Count creates, edits, deletes,
   added/deleted lines, and files with unresolved merge conflicts.
2. Run already available tools in check-only mode against a temporary checkout or with caches
   redirected outside the target. Count baseline rule violations by ID and path; mark an estimate
   `unknown` where safe execution or a candidate checker is unavailable.
3. Produce three views from the same complete rule ledger: whole repository, new/changed code
   relative to the recorded base ref, and user-selected paths. The scope may change which files are
   gated, never which applicable rules must exist.
4. Estimate setup dependencies and added local/CI runtime from measured commands when possible.
   Label measured values separately from estimates and state their cache/platform assumptions.
5. For each view, report missing implementations, failing canaries, unresolved existing-rule
   conflicts, and the work needed to reach green. Include the full-plan total even if a narrower
   rollout is recommended.
6. Keep candidate files and command output temporary and outside protected paths. Do not run fix
   mode, install packages, stage files, or write a retained summary into the target during dry run.

## Done when

- Whole-repo and scoped file/line/violation estimates are visible with confidence and unknowns.
- The user can compare full adoption against a new/changed-code or selected-path rollout without
  hidden rule omissions.
