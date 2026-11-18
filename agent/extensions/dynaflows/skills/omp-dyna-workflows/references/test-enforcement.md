# Test enforcement

These dynaflows checks implement the linked generic guidance. The rule catalogue at
`agent/extensions/dynaflows/guardrails/omp_guardrails/data/guardrail-rules.json` defines contracts,
diagnostics and subcommands; generic test-guidance does not depend on these tools.

- PROFILE.complete (omp-guardrails profile-check) → skill://test-guidance/references/tiers.md#start-here
- TEST.fast-tier-isolation (omp-guardrails guard-run) → skill://test-guidance/references/tiers.md#membership
- TEST.tier-selectors (omp-guardrails collection-check) → skill://test-guidance/references/tiers.md#membership
- TEST.placement (omp-guardrails path-check) → skill://test-guidance/references/suite-budget.md#placement
- NAME.test-files (omp-guardrails naming) → skill://test-guidance/references/naming-and-layout.md#guidance
- NAME.conventions (omp-guardrails naming) → skill://test-guidance/references/naming-and-layout.md#guidance
- TEST.property-seed (omp-guardrails adapter) → skill://test-guidance/references/property-tests.md#guidance
- TEST.mutation-sensitivity (omp-guardrails adapter, mutation-check) → skill://test-guidance/references/property-tests.md#guidance
- TEST.no-self-mocking (omp-guardrails adapter) → skill://test-guidance/references/outcome-tests.md#guidance
- TEST.no-test-hooks (omp-guardrails adapter) → skill://test-guidance/references/outcome-tests.md#guidance
- TEST.subprocess-timeouts (omp-guardrails python-test-hygiene) → skill://test-guidance/references/outcome-tests.md#guidance
- TEST.user-data-guard (omp-guardrails guard-run) → skill://test-guidance/references/outcome-tests.md#guidance
- TEST.coverage-ratchet (omp-guardrails diff-check) → skill://test-guidance/references/outcome-tests.md#coverage
- TEST.growth.justified (omp-guardrails diff-check) → skill://test-guidance/references/suite-budget.md#justified-growth
- TEST.duplicate-path (omp-guardrails diff-check) → skill://test-guidance/references/suite-budget.md#duplicate-paths
- TEST.budget.per-test-time (omp-guardrails budget-check) → skill://test-guidance/references/suite-budget.md#per-test-time
- TEST.budget.fast-tier-time (omp-guardrails budget-check) → skill://test-guidance/references/suite-budget.md#fast-tier-time
- TEST.identity-artefacts (omp-guardrails diff-check) → skill://test-guidance/references/before-commit.md#guidance
- TEST.failure-propagation (omp-guardrails report-check, collection-check) → skill://test-guidance/references/before-commit.md#guidance
- TEST.no-evasion (omp-guardrails adapter, report-check, diff-check) → skill://test-guidance/references/before-commit.md#guidance
- TEST.golden-change-record (omp-guardrails diff-check) → skill://test-guidance/references/determinism.md#guidance
- SURFACE.lever-isolation (omp-guardrails lever-selftest) → skill://test-guidance/references/verification-surface.md#guidance
- SURFACE.feature-map (omp-guardrails surface-lint) → skill://test-guidance/references/verification-surface.md#feature-map-and-journeys
- SURFACE.journeys (omp-guardrails surface-lint) → skill://test-guidance/references/verification-surface.md#feature-map-and-journeys

TEST.growth.justified uses descriptive test names stating the behaviour, not inline behaviour comments.
Test files follow the same comment ban as product code.

The dynaflows review task owns test-name truthfulness outside the presence-only check
(`truthfulness_sampled_by_jev_judge_batch_outside_check`). Use `judge_batch()` to sample up to 20
new test names against their assertions. If more than 2 are vacuous, run `judge_batch()` over every
new test name; rename, rewrite or remove each mismatch before delivery.
