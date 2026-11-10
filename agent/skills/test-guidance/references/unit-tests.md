# T1 unit-test patterns

## Objective

Test decisions in memory with injected dependencies, deterministic execution and behavioural coverage.

## Guidance

- Cover caller-visible returns, records, errors, exit codes and diagnostics at the owning T1 seam.
  Extend existing tests before adding new ones; do not duplicate assertions across tiers.
- Extract pure decisions when code mixes computation with process, socket, file or clock I/O.
  Test those decisions in memory and keep the I/O shell thin.
- Inject the package's existing clock or advance the system a fixed number of steps.
  Do not borrow another application's private clock or sleep in a T1 test.
- Assert "after N steps, X", not "within elapsed wall time, approximately N steps".
  For thresholds, cover the step before, the step at the threshold and the reset case.
- Drive process and file shells through the repo's injected runner interface.
  Cover each handled result separately: success, non-zero exit, timeout and malformed output.
  Real I/O belongs only in the wrapper's own conformance test where repo tier policy permits it;
  otherwise place real-boundary coverage in the boundary tier.
- Build inputs in memory.
  Write files only when their format is under test or the owning test rig requires them;
  isolate them in a temporary directory unique to the run.
- Drive caller input through the same public input or client seam that production callers use.
  At T1, use the owning package's scripted client or injected equivalent, not private state writes.
- Inject dependencies; do not mock modules.
  Never assert that a fake merely echoes the input the test supplied.
  Assert what the code under test computed, decided or emitted from that input.
- Set timeouts on the object that actually waits: the runner call, child handle or drive loop.
  A test-level timeout alone does not bound or cancel the underlying wait.
- Keep one scenario per test when driving a scenario to completion.
  Use only as many steps as the assertion needs, within the repo's per-test cap on its CI runner.
- Inspect per-test durations with the repo's runner duration report.
  A slow T1 test commonly hides real I/O, a real clock, an unbounded wait or unnecessary steps.
  Fix the cause; never raise the timeout or budget to get a pass.
- Reach the repo's coverage threshold through tests of behaviour, including branches and errors.
  Keep untested owned files in the coverage denominator.
  Delete genuinely unreachable code instead of excluding it; never lower thresholds.
- Add no unowned coverage exclusion.
  An allowed exclusion must follow repo policy, name the owning boundary or conformance coverage,
  and remain checked by that owner; a hard-to-test decision is not grounds for exclusion.
