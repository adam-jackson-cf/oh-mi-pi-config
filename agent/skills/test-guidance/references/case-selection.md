# Choose what to test

## Objective

Select realistic cases that distinguish required behaviour without duplicating assertions.

## Guidance

- Start from the feature-map entry, or the requirement for a new feature or a valid surface exemption.
  Name the user path and realistic input for every case; drop an impossible case.
- Per feature, cover boundary-crossing journey end states, user-reachable errors, side effects and preserved neighbours.
  Use `lsp references` and the repo test profile's affected-T2 selection to name affected neighbours.
- Per pure core, select domain edges, thresholds, ordering and independent-reference or round-trip properties.
  For a pure-core bug, add a property; add a T1 outcome case only for caller behaviour not covered by it or the journey.
- Per external adapter, cover success, failure, timeout and malformed responses through the production input.
  For gates and checkers prove both acceptance and refusal.
- Exclude wiring-only cases, fake echoes, single bug-value pins, private implementation details and prose no caller reads.
- Stop at one case per behaviour and distinct input class that changes the outcome; map every case to its behaviour.
  Prefer no new test over a bad test.
- Extend an existing scenario rather than duplicating it.
- For a regression, cover representative members of the failure class rather than only the reported input.
  Check whether existing behavioural tests cover the broken rule; report the failing behaviour and whether coverage protected it.
  When coverage is missing, expand the relevant existing scenario to protect the intended outcome, not the incidental value.
  If no scenario covers the area, add one outcome case for the intended behaviour (shown failing first)
  and report the coverage gap; never add a test that pins only the fixed value.
  Never add duplicate or narrowly tailored cases that pin only one bug-fix value, example or changed text.
- Apply [outcome-tests.md](outcome-tests.md) to kept cases and [property-tests.md](property-tests.md) to pure cores.
