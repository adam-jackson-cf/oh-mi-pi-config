# Name and place tests

## Objective

Give each test a discoverable behaviour name and an existing ownership home.

## Guidance

- Read Layout and naming in the repo test profile: Support directory, Fixtures directory and Test file patterns.
  Use Verification surface's Journey file pattern for journeys. Missing values are profile gaps, not invented defaults.
- Mirror the owning module or feature in each test file name. Name a test for behaviour + condition + outcome.
  Enforced by NAME.test-files (omp-guardrails naming).
- Enforced by TEST.property-seed (omp-guardrails adapter).
- Follow sibling naming patterns. Enforced by NAME.conventions (omp-guardrails naming).
- Enforced by TEST.placement (omp-guardrails path-check).
- Group by owner. Create a folder with its first file, never empty scaffolding.
- Use [tiers.md](tiers.md) for tier membership and assertion ownership.
  Enforced by TEST.tier-selectors (omp-guardrails collection-check).
