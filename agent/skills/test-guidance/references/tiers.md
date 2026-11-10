# Test tiers and layout

Place each test by what it drives; split decisions from boundary wiring; one owner per assertion.

- The project's own test skill or AGENTS.md test rules take precedence; this fills gaps.
- Where this is silent: keep existing content, make no irreversible write, ask the user once when the choice changes the outcome, state the choice in the result.
- Pick the tier by what the test drives, never by duration. Map these tiers onto the repo's existing tiers; never create parallel ones.

## Tiers

- **T1 unit**: decisions, codecs, schemas, parsers and checkers in memory; also a thin process or file shell through an injected clock or runner.
  Follow the repo's unit-test layout, coverage threshold and per-test time budget.
- **T2 boundary**: a real process, build, transport, storage or external-service boundary.
  Seam test for a boundary contract; journey test for a caller-visible path across the boundary.
  Keep T2 outside the fast tier; use repo-owned boundary fixtures and isolated resources.
- **T3 scheduled/on-demand**: soak, broad sweeps, resource-budget benchmarks. Never move a test to T3 to escape a T1 or T2 limit.

## Assertion ownership

- T2 proves only what crosses the boundary: what another process receives, what survives restart, or a stable public diagnostic identifier.
  Without a stable identifier, assert the observable refusal or recovery result, never error prose or a bare throw that could pass for the wrong reason.
- Exact branch, computed-value and decision-error assertions belong in the owning T1 test, with the exact expected value and no boundary-added decoration.
  Tighten an existing case before adding one; extract the decision if necessary.
- Behaviour needing both tiers: T1 proves the decision in memory; T2 proves only wiring the injected T1 seam cannot.
  When an injected timer or runner suffices, test decision and wiring at T1 with no T2 case.
  Caller-facing behaviour alone never justifies a journey when no real boundary is involved.
- A moved test takes its fixtures and leaves no duplicate. Each assertion has one owner across tiers.

## Layout

- Follow the owning package's test and support layout and naming.
  Name new tests for the module or behaviour they prove; place fixtures with their test owner.
- Create a directory with its first file, never as empty scaffolding.
- This is guidance only: no new tier, runner or gate, and it replaces no existing mechanism.
