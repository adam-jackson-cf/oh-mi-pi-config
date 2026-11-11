# Property tests

## Objective

Prove pure-core properties against an independent oracle over generated realistic inputs.

## Guidance

- Use property tests only on pure cores. Reach other code through [outcome-tests.md](outcome-tests.md).
- Compare generated inputs against an independent reference; use wider or exact arithmetic when needed.
  Include domain edges, overflow and invalid-input boundaries. Round trips complement independent references;
  paired encode/decode bugs can preserve a round trip.
- For order-insensitivity, shuffle generated inputs and compare semantic results.
- Enforced by TEST.property-seed (omp-guardrails adapter).
  Report the input sequence. Reproduce failures from seed and shrink path.
  Retain the shrunk counterexample as a named example.
- Enforced by TEST.mutation-sensitivity (omp-guardrails adapter, mutation-check).
- Use bounded model checking in T3 for races or state machines; do not turn a pure-core property into a live-service test.
