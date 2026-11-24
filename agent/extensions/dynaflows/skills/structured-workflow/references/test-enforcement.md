# Test enforcement

Each rule enforces the linked generic guidance. The rule catalogue at
`agent/extensions/dynaflows/engine/guardrails/data/guardrail-rules.json` defines contracts,
diagnostics and subcommands.

- PROFILE.complete (guardrails config-check) → skill://test-guidance/references/tiers.md#start-here
- TEST.fast-tier-isolation (guardrails guard-run) → skill://test-guidance/references/tiers.md#membership
- TEST.tier-selectors (guardrails collection-check) → skill://test-guidance/references/tiers.md#membership
- TEST.placement (guardrails path-check) → skill://test-guidance/references/suite-budget.md#placement
- NAME.test-files (guardrails naming) → skill://test-guidance/references/naming-and-layout.md#guidance
- NAME.conventions (guardrails naming) → skill://test-guidance/references/naming-and-layout.md#guidance
- TEST.property-seed (guardrails adapter) → skill://test-guidance/references/property-tests.md#guidance
- TEST.mutation-sensitivity (guardrails adapter, mutation-check) → skill://test-guidance/references/property-tests.md#guidance
- TEST.no-self-mocking (guardrails adapter) → skill://test-guidance/references/outcome-tests.md#guidance
- TEST.no-test-hooks (guardrails adapter) → skill://test-guidance/references/outcome-tests.md#guidance
- TEST.subprocess-timeouts (guardrails python-test-hygiene) → skill://test-guidance/references/outcome-tests.md#guidance
- TEST.user-data-guard (guardrails guard-run) → skill://test-guidance/references/outcome-tests.md#guidance
- TEST.coverage-ratchet (guardrails check, diff-check) → skill://test-guidance/references/outcome-tests.md#coverage
- TEST.growth.justified (guardrails check, diff-check) → skill://test-guidance/references/suite-budget.md#justified-growth
- TEST.duplicate-path (guardrails check, diff-check) → skill://test-guidance/references/suite-budget.md#duplicate-paths
- TEST.budget.per-test-time (guardrails budget-check) → skill://test-guidance/references/suite-budget.md#per-test-time
- TEST.budget.fast-tier-time (guardrails budget-check) → skill://test-guidance/references/suite-budget.md#fast-tier-time
- TEST.identity-artefacts (guardrails check, diff-check) → skill://test-guidance/references/before-commit.md#guidance
- TEST.failure-propagation (guardrails report-check, collection-check) → skill://test-guidance/references/before-commit.md#guidance
- TEST.no-evasion (guardrails adapter, report-check, diff-check) → skill://test-guidance/references/before-commit.md#guidance
- TEST.golden-change-record (guardrails check, diff-check) → skill://test-guidance/references/determinism.md#guidance
- SURFACE.lever-isolation (guardrails lever-selftest) → skill://test-guidance/references/verification-surface.md#guidance
- SURFACE.feature-map (guardrails surface-lint) → skill://test-guidance/references/verification-surface.md#feature-map-and-journeys
- SURFACE.journeys (guardrails surface-lint) → skill://test-guidance/references/verification-surface.md#feature-map-and-journeys

`guardrails check` runs these five rules and the CODE.* rules at commit over the staged changes against HEAD;
`guardrails diff-check` runs them over an explicit `--base`/`--head` range. `prove()` runs none of them.

TEST.growth.justified uses descriptive test names stating the behaviour, not inline behaviour comments.
Test files follow the same comment ban as product code.

Before delivery, the implementation task samples test-name truthfulness outside the presence-only
check (`truthfulness_sampled_by_jev_judge_batch_outside_check`). Use `judge_batch()` to sample up to 20
new test names against their assertions. If more than 2 are vacuous, run `judge_batch()` over every
new test name; rename, rewrite or remove each mismatch before delivery.
