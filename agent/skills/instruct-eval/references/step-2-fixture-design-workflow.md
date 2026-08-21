# Design fixtures and checkpoint requests

## Objective

Create varied scenarios that expose compliance, shortcuts, future change cost, and overcorrection.

## Required actions

1. Produce at least three fixture options: shortcut pressure, change pressure, and overcorrection control.
2. Select scenarios with distinct failure mechanisms rather than textual variants.
3. Define an initial request and one or more checkpoint requests for each scenario.
4. Specify pristine fixture files, public behavior, hidden invariants, preferred architecture, and disallowed shortcuts.

## Done when

- Every frozen scenario in `protocol.json` records a distinct failure mechanism, public behavior, hidden invariants, disallowed shortcuts, initial request, and checkpoint requests.
- No unresolved validation error remains before the next workflow step.
