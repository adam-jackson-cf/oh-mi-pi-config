# Rule coverage verification

## Objective

Prove that no applicable rule is merely named, skipped, or disconnected from
execution.

## Guidance

1. With a skill reference, run the target's pinned `omp-guardrails
   rule-coverage` at the repository root. It compares the ledger with the
   catalogue packaged in that pin; the target tracks no catalogue copy. Expected
   exit: 0 only when every always-applicable rule the package does not prove
   has exactly one valid repository-checker row and each row's canary and hook
   installation check pass. It rejects missing or duplicate rows, rows for
   rules the package proves, unmarked non-catalogue IDs, evidence kinds that
   differ from the catalogue, absent implementation or runner paths, and failed
   canaries or hook installation checks. Use the catalogue's
   [evidence kind](../README.md#coverage-evidence-kinds); hook rules do not
   claim CI execution. `omp-guardrails rule-evidence` checks the rows without
   running them; it is planning evidence, never final proof. Without a
   reference, prove every applicable rule with the stack's own checkers.
2. Run positive and negative checker/hook canaries for every repository
   checker. On a stack without a reference, do so for every applicable local/CI
   or hook ID. For `STRUCT.dependency-direction`, allow a legal edge and reject
   a reversed/unmapped edge. For `STRUCT.folder-placement`, accept an owned path
   and reject a misplaced/unmapped file. For `NAME.conventions`, accept valid
   path/symbol names and reject invalid ones. For `TEST.placement`, accept an
   owner/tier path and reject a misplaced test. Assert that each failure names
   the file and exact corrective action. With a reference, the skill's own
   tests prove tool rules and rules with a `reference` field; never run a
   copied checker.
3. Prove the target's wiring of test-evidence rules: `checks.commit` runs the
   budget checker and blocks on its failure, and `checks.ci` never runs it.
   On a stack without a reference, also prove the checkers themselves. For
   `TEST.budget.fast-tier-time` and `TEST.budget.per-test-time`, feed valid
   below-limit, exactly-at-limit and over-limit reports to the real checker.
   Prove machine selection and blocking against approved limits. Prove first
   limits and raises need owner approval; unapproved machines prompt for a
   baseline and report timing only. Missing, invalid or stale reports and
   missing measurements fail. For `TEST.growth.justified`, accept a new
   file/case mapped to changed source and behavior; reject unmapped or
   unresolved mappings with the test name and map-or-remove fix. For
   `TEST.duplicate-path`, accept distinct fingerprints and reject a new case
   sharing an existing/other new case's call path and assertion; require both
   names and merge-or-remove fix. With a reference, the skill's own tests
   prove these checkers; never feed them per-repo fixtures.
4. Parse `.omp/gate.json` and verify Step 6's schema, version, field types,
   command arrays, timeout, globs, and merge-policy enum/defaults.
   Optional fields must remain optional to the format validator. Prove the
   target's gate wiring: actual CI runs `checks.ci`; for parallel CI, the
   aggregate depends on every tier and fails on any failed, cancelled, or
   skipped result, not just that tier selectors cover `checks.ci`. In an
   isolated checkout, the hook is installed, a passing `checks.commit` permits
   the commit and a failing one blocks it with the same status. Never test by
   pushing to a protected branch. On a stack without a reference, also prove
   the checkers: missing rule-required commands fail the coverage check;
   `GATE.local-ci-parity` with matching/divergent `checks.ci` argv fixtures;
   `GATE.path-check` with present/missing command fixtures and
   clean/violating paths, asserting appended argv, nonzero failure, and one
   actionable file/fix error per output line. With a reference, the skill's
   own tests prove `GATE.local-ci-parity`, `GATE.pre-commit` and
   `GATE.path-check`.
5. Verify tool selectors, limits, exceptions, and pinned versions. Run focused
   checker tests, `checks.commit`, `checks.ci` (or its CI tier jobs), and
   local/CI parity tests. In an isolated canary environment, confirm
   disabling/corrupting a repository checker makes coverage fail. Do not count
   skipped, unavailable, or timed-out checks as passes. Verify actual CI on the
   final checked commit when approved.
6. Rerun coverage after repairs; compare final paths with the approved dry-run
   matrix. Confirm superseded contracts were removed or record why nothing could
   be removed. Return the specified terminal format with rule IDs, scope,
   commands/results, change counts, documentation changes, and blockers. Do not
   save a retained summary artifact.

## Done when

- Every applicable rule is implemented, invoked, and proven; no blocked rule is
  called green.
- Gate validity, fast/full gates, local/CI parity, and approved scope pass or
  have concrete blockers.
