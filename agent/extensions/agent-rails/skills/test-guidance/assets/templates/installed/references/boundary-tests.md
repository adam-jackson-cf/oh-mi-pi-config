# Boundary tests and journeys

Read this when adding or changing a T2 boundary test or journey.

## Assertions

- Assert only what crosses the boundary: what another process receives, what survives a restart, exit codes,
  or a stable diagnostic identifier. Without an identifier, assert the observable refusal or recovery, never error prose
  or a bare exception that could pass for the wrong reason.

## Running real processes

- Launch the product as the skill's boundary launch line states.
- Put a positive timeout on every process call and async wait in shared fixtures too.
- Give each test its own data directory and ports. Never drive a shared user instance.
- Probe a required environment in the first test that needs it, never at collection, so a missing environment fails instead of skipping.

## Choosing what to run

- Select the T2 tests a change affects by following references to the changed symbols transitively,
  including through entry points a T2 test launches. When you cannot tell, run all T2 and say so.
- Run each new or changed journey at least three times and count only clean runs, rejecting hangs and contaminated state.
  Unchanged T2 tests run once.
