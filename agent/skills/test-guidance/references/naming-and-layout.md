# Name and place tests

## Objective

Give each test a discoverable behaviour name and an existing ownership home.

## Guidance

- Read Layout and naming in the repo test profile: Support directory, Fixtures directory and Test file patterns.
  Use Verification surface's Journey file pattern for journeys. Missing values are profile gaps, not invented defaults.
- Mirror the owning module or feature in each test file name. Name a test for behaviour + condition + outcome.
  Test file names match the profile's Test file patterns and contain no ticket, version or date tokens.
- Follow sibling naming patterns.
- Group by owner. Create a folder with its first file, never empty scaffolding.
- Use [tiers.md](tiers.md) for tier membership and assertion ownership.
