# Name and place tests

## Objective

Give each test a discoverable behaviour name and an existing ownership home.

## Guidance

- Read Layout and naming in the repo test profile: Support directory, Fixtures directory and Test file patterns.
  Use Verification surface's Journey file pattern for journeys. Missing values are profile gaps, not invented defaults.
- Mirror the owning module or feature in each test file name. Name a test for behaviour + condition + outcome.
  Do not use ticket, version or date names. Include a property's seed in its title or failure message.
- Follow sibling naming patterns and the ecosystem's casing. Place support and fixtures at the profile's locations.
- Group by owner; do not create grab-bag folders named common, utils, misc or helpers.
  Create a folder with its first file, never empty scaffolding.
- Use [tiers.md](tiers.md) for tier membership and assertion ownership; never mix tiers within one file.
