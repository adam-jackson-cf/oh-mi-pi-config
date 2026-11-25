# Property tests

Read this when adding or changing a property test, or changing code whose job is a rule over a whole input domain.

## When to write one

- Use property tests only on pure cores, and only for code whose job is a rule over a whole input domain: ranges, ordering, encoding,
  or numeric representation (a money or fixed-point type, overflow handling, a rounding mode).
- A threshold, rate or lookup rule, including the rounding its formula does, needs only outcome cases.
  Add a case at a rounding edge only when the requirement states the rounding; when it is silent and rounding changes a result,
  report the unspecified rounding instead of pinning the implementation's choice.

## Writing one

- Use the library the skill's property library line names, following any existing property tests.
- Compare generated inputs against an independent reference implementation, using wider or exact arithmetic when that gives
  a trustworthy oracle. Include domain edges, overflow and invalid-input boundaries.
- A round trip complements an independent reference but never replaces it: paired encode and decode bugs can preserve a round trip.
- Keep the reference with the owning test support code.
- For order-insensitive functions, shuffle generated inputs and compare semantic results.
- Set or report the generator seed in every property test.

## When one fails

- Reproduce it from the reported seed and shrink path, keep the shrunk counterexample as a named example test beside the property,
  then fix the implementation. That example is not a single-value pin: the property covers the class.
- Use a named example, not a property, for a single malformed record. Moving or tidying a test adds no new property.

## Sensitivity

- Check that plausible defects in the claimed property make the test fail.
- Never turn a pure-core property into a live-service test.
