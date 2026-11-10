# Determinism tests

## Objective

Prove that the same seed and inputs yield the same state across runs, hosts, ordering and replay.

## Guidance

- Follow the repo's purity and execution rules; this reference governs their test evidence.
- Pass every seed explicitly and name it in the test title or failure message.
  Preserve the input sequence and execution options needed to reproduce the run.
- Compare per-step state hashes when claiming determinism, not only final outcomes.
  Run the same seed twice and, where relevant, on each supported host; compare every step.
- Test host parity and replay through the applicable boundary tier.
  Replay the recorded input log and compare the same state sequence.
  A change to stepping, hashing or logging extends the cross-host or cross-restart case.
  Test caller-visible decoding errors and computed values through the public entry point at T1;
  use [property-tests.md](property-tests.md) for pure-core properties.
- For arithmetic and codec changes, use generated-input property tests against an independent
  reference implementation, including edge values and overflow or invalid-input boundaries.
  Use a wider or exact numeric representation when it provides a trustworthy arithmetic oracle.
  A round-trip property complements, but does not replace, a reference that can reveal paired bugs.
- Keep the reference with the owning test support code and follow existing property-test patterns.
  Use a named example for a single malformed record; moving or tidying a test adds no new property.
- For order-insensitive functions, shuffle generated inputs and assert the same semantic result.
- When a property fails, reproduce it using the generator's reported seed and shrink path.
  Keep the shrunk counterexample as a named example test, then fix the implementation.
- Change a golden only when an intended domain or protocol rule change justifies it.
  Enforced by TEST.golden-change-record (omp-guardrails diff-check).
  The recorded reason and PR body name the first divergent step and whether externally visible outcomes changed.
- A golden that changes without an intended rule change is a failure to investigate.
  Never regenerate expected output merely to make a test pass.
  Use the repo's established golden verification and regeneration commands.
