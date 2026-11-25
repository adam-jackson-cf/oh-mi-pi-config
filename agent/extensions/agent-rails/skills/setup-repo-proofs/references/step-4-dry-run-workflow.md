# Dry-run impact

## Objective

Show the full cost of enforcing every applicable rule, for each rollout scope, before changing the target.

## Required actions

1. Render every candidate file under the temporary candidate root and compare it with its target counterpart using Git diff or
   equivalent exact file comparison. Count creates, edits, deletes, added/deleted lines and files with unresolved merge conflicts.
2. List each scope's files with `python3 <skill>/scripts/list_scope.py --repo <temporary clone> <mode> [--exclude <glob> ...]`
   (expect exit 0; exit 2 means a bad repo, ref or path):
   - `whole` for the whole repository minus evidenced exclusions;
   - `changed --base <ref>` for new and changed code relative to the recorded base ref (`--new-paths` for naming and structure rules,
     which judge only added and renamed paths); the clone lacks the target's uncommitted work, so also list `changed` on the
     target (read-only) and report its extra paths;
   - `paths <path> ...` for owner-selected paths.
3. Run existing tools and candidate checkers in check-only mode against the temporary clone, or with caches redirected outside the
   target. Count findings by rule ID and path, then partition them by scope using the action 2 lists.
   Families given to repo checkers (`keep`) run through the repo's own enforcement entrypoint; report that command's result with them.
   Run the candidate structure, naming and placement checks over the candidate added and renamed paths and report their findings separately.
   Mark an estimate `unknown` where safe execution or a candidate checker is unavailable, never zero.
4. Produce three views from the same complete rule mapping: whole repository, new/changed code, and selected paths.
   A scope changes which files are gated, never which applicable rules must exist. The whole-repository view is always shown:
   a narrower scope never hides findings, and no view uses a file of waived findings.
5. Run `python3 <skill>/scripts/validate_rule_coverage.py --root <temporary clone> --proofs <candidate root>/proofs.toml
   --catalog <skill>/assets/guardrail-rules.json --structure-only` (exit 0 prints `proofs STRUCTURE_ONLY`; exit 1 lists every
   missing rule, unbound claim and malformed entry). Report its output as planning evidence, never as proof.
6. Estimate setup dependencies and added local/CI runtime from measured commands when possible. Label measured values separately
   from estimates and state cache and platform assumptions.
7. For each view, report missing implementations, failing canaries, unresolved existing-rule conflicts and the work needed to reach
   zero findings. Include the full-plan total even when a narrower rollout or rule set is recommended.
8. Keep candidate files, the draft `proofs.toml` and command output in the candidate root, and write the three views and costs to
   `state.json` `dry_run` ([run state](run-state.md)). Install the tools the checks need only into an environment under the candidate
   root (for example `uv sync` into a virtual environment there, or `uv run --with <tool>`), never into the target. Do not run fix
   mode or stage files during the dry run.

## Done when

- Whole-repository and scoped findings, file and line counts, costs, confidence and unknowns are visible.
- The owner can compare full adoption with a new/changed-code or selected-path rollout, and Minimal with Full, without hidden rule omissions.
