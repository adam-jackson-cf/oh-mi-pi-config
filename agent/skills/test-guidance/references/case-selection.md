# Choose what to test

## Objective

Select realistic cases that distinguish required behaviour without duplicating assertions.

## Guidance

- Start from the feature-map entry, or the requirement for a new feature or a valid surface exemption.
  Name the user path and realistic input for every case; drop an impossible case.
- Per feature, cover boundary-crossing journey end states, user-reachable errors, side effects and preserved neighbours.
  Use codegraph impact when available to name affected neighbours.
- Per pure core, select domain edges, thresholds, ordering and independent-reference or round-trip properties.
  For a pure-core bug, add a property; add a T1 outcome case only for caller behaviour not covered by it or the journey.
- Per external adapter, cover success, failure, timeout and malformed responses through the production input.
  For gates and checkers prove both acceptance and refusal.
- Exclude wiring-only cases, fake echoes, single bug-value pins, private implementation details and prose no caller reads.
- Stop at one case per behaviour and distinct input class that changes the outcome; map every case to its behaviour.
  Prefer no new test over a bad test.
- Extend an existing scenario unless the active DynaFlows playbook's freezing rules require a dedicated proof file.
  The Bug fix playbook's small-dedicated-file rule wins for bugs; other playbooks use the extension default
  unless their proof-file scope requires isolation. Whole frozen files must remain immutable during implementation.
- For a regression, cover representative members of the failure class rather than only the reported input;
  apply the calling workflow's regression-coverage guidance.
- Apply [outcome-tests.md](outcome-tests.md) to kept cases and [property-tests.md](property-tests.md) to pure cores.
