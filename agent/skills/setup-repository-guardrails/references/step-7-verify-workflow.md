# Rule verification

## Objective

Prove that no applicable rule is merely named, skipped, or disconnected from
execution.

## Guidance

1. Run the harness checks from the harness over the whole repository with the
   record, as rails do at commit: `uv run --project
   <omp root>/agent/skills/setup-repository-guardrails/assets/python-reference
   omp-guardrails check --repo <worktree> --record <record dir>`. Expected exit
   0: zero findings outside the record's
   scope exclusions, with JUnit and temporary output outside the worktree and
   no file written into it. `omp-guardrails`' own tests in oh-mi-pi-config
   prove the checkers once for every repository.
2. For each repo-owned checker the project keeps for itself, run its own
   tests. For `STRUCT.dependency-direction`, `STRUCT.folder-placement`,
   `NAME.conventions` and `TEST.placement` implemented by the repo's own path
   command, assert that each failure names the file and exact corrective
   action.
3. Prove the record's test-evidence wiring: the harness runs the budget
   checker at the agent's commit and blocks on its failure, and `checks.ci`
   never runs it. Prove machine selection against the record's approved
   limits; an unapproved machine prompts for a baseline and reports timing
   only.
4. Parse the record's `gate.json` and verify Step 6's schema, version, field
   types, command arrays, globs, merge-policy enum/defaults and a boolean
   `dynaflows.enabled`. Optional fields must remain optional to the format
   validator. Prove the target's own wiring: actual CI runs `checks.ci`; for
   parallel CI, the aggregate depends on every tier and fails on any failed,
   cancelled, or skipped result, not just that tier selectors cover
   `checks.ci`. In an isolated checkout, a passing `checks.commit` with zero
   harness findings permits an agent commit, and a failing one blocks it.
   Never test by pushing to a protected branch.
5. Verify tool selectors, limits, exceptions, and pinned versions. Run
   `checks.commit`, `checks.ci` (or its CI tier jobs), and local/CI parity
   tests. Do not count skipped, unavailable, or timed-out checks as passes.
   Verify actual CI on the final checked commit when approved.
6. Confirm `git status` in the target shows no record, proposal, JUnit or
   other harness output, and that `git grep -n -i -w -E 'omp|omp-guardrails|dynaflows'`
   finds no harness mention the change added. Rerun the harness checks after repairs;
   compare final paths with the approved dry-run matrix. Confirm superseded
   contracts were removed or record why nothing could be removed. Return the
   specified terminal format with rule IDs, commands/results, change counts,
   documentation changes, and blockers. Do not save a retained summary
   artifact.

## Done when

- Every applicable rule is implemented, invoked, and proven; no blocked rule is
  called green.
- The harness checks report zero findings over the whole repo, and record
  validity, local/CI parity, and approved scope pass or have concrete blockers.
