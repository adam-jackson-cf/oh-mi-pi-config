# Outcome tests

## Objective

Prove caller-visible outcomes through the public entry point with realistic inputs.

## Guidance

- Tie each outcome test that proves a feature to that feature's feature-map entry by a link from the entry
  ([verification-surface.md](verification-surface.md#feature-map-and-journeys)).
- Read Tiers' Public entry point per package in the repo test profile. Drive it with concrete realistic inputs.
  Derive the expected value independently from the requirement, never by copying the implementation's output.
- For every kept test imagine two edits: a harmless internal refactor must leave it green, and breaking its claimed
  behaviour must make it fail. Rewrite or delete a test that fails either imagined check.
- Assert caller-visible returns, records, errors callers branch on, exit codes, messages or files.
  Never assert private state or call counts merely to prove wiring.
- Do not runtime-patch modules, subprocesses or clients.
  Do not mock, stub or fake the repo's own code.
- Clock, randomness, ID generation, process runner and external-service client must be production inputs/adapters
  justified by production use. Supply deterministic values through those same inputs.
  Adding an input is a production design change, justified in the PR.
- Never add test-only flags, environment variables, hooks or marker names to production code,
  or a production symbol that only tests reference; supply test values through production inputs.
- Use recorded or local external-service substitutes behind the production adapter.
  Live paid or LLM calls belong only in T3.
- Assert fixed-step time: after N steps, X. Cover before, at and reset for thresholds.
  Put timeouts on the actual waiting object, not merely the test wrapper.
  Give every synchronous subprocess call and every process wait or communicate call a positive timeout,
  and bound every async process wait, including in shared fixtures.
- Isolate in-process storage in a unique temporary directory.
  Run tests with HOME and XDG directories pointed at a temporary directory; any change to a file
  under the real user home outside that temporary directory is a failure.
- Use only the steps the assertion needs.
  Without a per-test limit, over 100 ms is an investigation hint for I/O or excess steps, not an invented blocking cap.

## Coverage

- Keep all owned files in the denominator and retain existing owned-exclusion rules.
  Diagnostic coverage spans T1 + T2, or explicitly classifies lines T2 owns. T1-only gaps do not prove dead code.
- Meet enforced Coverage floors only with outcome cases, property cases or deleting proven-unreachable code.
  If this cannot meet a floor, stop coverage work and ask the owner once:
  "What requirement or product change should resolve this unmet coverage floor?" Include floor, scope and evidence.
  Resume coverage work only on that decision; rejection or no answer leaves the floor unmet and delivery blocked.
  Never test internals.
- Add no coverage exclusions or no-cover pragmas (e.g. `# pragma: no cover`, `/* istanbul ignore */`);
  lowering a floor or changing exclusion configuration needs a recorded owner approval.
- Record an uncovered known defect with its issue ID rather than executing it without an assertion.
