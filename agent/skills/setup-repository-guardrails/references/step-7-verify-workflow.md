# Rule coverage verification

## Objective

Prove that no applicable rule is merely named, skipped, or disconnected from
execution.

## Guidance

1. Run this skill's `scripts/validate_rule_coverage.py` with `--root <repo>`,
   `--catalog <skill>/assets/guardrail-rules.json`, and
   `--ledger <approved-ledger>`. Expected exit: 0 only for complete coverage
   with approved conditional exclusions. It must reject missing/duplicate IDs,
   unjustified exclusions, evidence kinds that differ from the catalog, absent
   required implementation/runner paths, failed canaries or hook installation
   checks, and missing host-query assertions. Use the catalog's
   [evidence kind](../README.md#coverage-evidence-kinds); hook rules do not
   claim CI execution and host rules require recorded live queries rather than
   runner paths or fixture canaries. `--structure-only` is planning evidence,
   never final proof.
2. Run positive and negative checker/hook canaries for every applicable local/CI
   or hook ID. For `STRUCT.dependency-direction`, allow a legal edge and reject
   a reversed/unmapped edge. For `STRUCT.folder-placement`, accept an owned path
   and reject a misplaced/unmapped file. For `NAME.conventions`, accept valid
   path/symbol names and reject invalid ones. For `TEST.placement`, accept an
   owner/tier path and reject a misplaced test. Assert that each failure names
   the file and exact corrective action.
3. For `TEST.budget.fast-tier-time` and `TEST.budget.per-test-time`, feed valid
   below-limit, exactly-at-limit, and over-limit runner JSON reports to the real
   checker. Confirm missing, invalid, stale reports and missing measurements
   fail, not silently pass. For `TEST.growth.justified`, accept a new file/case
   mapped to changed source and behavior; reject unmapped or unresolved mappings
   with the test name and map-or-remove fix. For `TEST.duplicate-path`, accept
   distinct fingerprints and reject a new case sharing an existing/other new
   case's call path and assertion; require both names and merge-or-remove fix.
4. Parse `.omp/gate.json` and verify Step 6's schema, version, field types,
   command arrays, timeout, globs, optional `sensitive` string array, and
   merge-policy enum/defaults. Preserve `sensitive` rather than dropping it.
   A pattern ending in `/` matches that folder anywhere in the path (`auth/`
   matches `auth/a.ts` and `src/auth/a.ts`); any other pattern matches when the
   file's basename starts with it.
   Test this rule and omitted/empty patterns; reject non-string entries.
   Optional fields must remain optional
   to the format validator; missing rule-required commands must fail the
   coverage check separately. Prove
   `GATE.local-ci-parity` with matching/divergent `checks.ci` argv fixtures and
   actual CI wiring. For parallel CI, prove the aggregate depends on every tier
   and fails on any failed, cancelled, or skipped result, not just that tier
   selectors cover `checks.ci`. Prove `GATE.pre-commit` in an isolated
   checkout: the hook is installed, a passing `checks.commit` permits the commit
   and a failing one blocks it with the same status. Never test by pushing to a
   protected branch. Prove `GATE.path-check` with present/missing command
   fixtures and clean/violating paths; assert appended argv, nonzero failure,
   and one actionable file/fix error per output line.
5. Test `GIT.required-checks`' checker against protected/unprotected
   host-settings fixtures; record live branch-protection readback requiring
   `gate` and other approved checks, plus auto-merge and rebase-merge where
   supported. Preserve query argv, exit status and stdout/stderr. A fixture
   alone cannot establish live host enforcement. Unsupported settings require a
   recorded host query, concrete exclusion reason and explicit owner approval.
6. Verify tool selectors, limits, exceptions, and pinned versions. Run focused
   checker tests, `checks.commit`, `checks.ci` (or its CI tier jobs), and
   local/CI parity tests. In an isolated canary environment, confirm disabling/corrupting a
   checker makes coverage fail. Do not count skipped, unavailable, or timed-out
   checks as passes. Verify actual CI on the final checked commit when approved.
7. Rerun coverage after repairs; compare final paths with the approved dry-run
   matrix. Confirm superseded contracts were removed or record why nothing could
   be removed. Return the specified terminal format with rule IDs, scope,
   commands/results, change counts, documentation changes, and blockers. Do not
   save a retained summary artifact.

## Done when

- Every applicable rule is implemented, invoked, and proven; no blocked rule is
  called green.
- Gate validity, fast/full gates, local/CI parity, and approved scope pass or
  have concrete blockers.
