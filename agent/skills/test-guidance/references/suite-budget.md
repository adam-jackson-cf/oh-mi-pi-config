# Suite budget

## Objective

Keep test growth justified, assertions singly owned and verification within approved machine budgets.

## Guidance

- Apply the repo test profile's Fast-tier budget, Tiers, Ownership areas and Command notes.
  This guidance adds no runner or numeric cap; fix policy violations rather than weaken existing enforcement.
  TEST.* headings name the guardrail rule IDs; retain them.

### TEST.growth.justified

- Enforced by TEST.growth.justified (omp-guardrails diff-check).
  Explain the coverage gap in the PR.
- Prefer extending an existing case, except when DynaFlows proof freezing requires a dedicated file.
  A new file, internal branch or implementation detail is not a new behaviour.
- Rewrite or delete tests that pass when their claimed behaviour breaks or fail only on harmless refactors,
  preserving real coverage first. Add no speculative cases outside the change's demonstrated blast radius.

### TEST.duplicate-path

- Enforced by TEST.duplicate-path (omp-guardrails diff-check).
- Apply [tiers.md](tiers.md) Assertion ownership; keep exact caller values at the public entry point
  or in pure-core properties, never internal decision tests. Shared setup does not justify duplicate assertions.

### TEST.placement

- Place tests by what they drive, following [tiers.md](tiers.md) and Layout and naming.
  Enforced by TEST.placement (omp-guardrails path-check).
- Enforced by TEST.fast-tier-isolation (omp-guardrails guard-run).
- Drive public entry points with deterministic production inputs; use property tests only for pure cores.
  Never extract internals merely for coverage. Never relabel a slow test to evade a cap.
- Enforced by TEST.no-test-hooks (omp-guardrails adapter).

### TEST.budget.per-test-time

- Enforced by TEST.budget.per-test-time (omp-guardrails budget-check).
  Budgets apply to the local commit gate only; CI never measures or reports test timing.
- When the checker reports an unknown machine, ask the owner once to record a baseline;
  approval permits recording the measured limit and reference; rejection leaves timing report-only.
  The first limit and every raise require owner approval; record its reference per machine.
  Derive proposed limits from repeated local measurements, never from a convenient single run.
- Fix excess cost: remove real-clock waits, bound the actual waiting object, isolate contention and minimize steps.
  Keep one scenario per test; never split redundant work merely to disguise its total cost.

### TEST.budget.fast-tier-time

- Enforced by TEST.budget.fast-tier-time (omp-guardrails budget-check).
  Compare baseline and head under comparable command, cache, runner and environment conditions.
  The owner-approval rules above apply to the wall limit.
- Remove redundant coverage, unnecessary work and wrong-tier execution before adding cost.
  Stop cutting when no further redundant or misplaced work is identifiable; never delete required coverage.
  If still over budget, report the commit blocked and ask the repo owner through the calling workflow once:
  "Approve a fast-tier wall limit of `<value>` on `<machine id>`, based on these repeated measurements: `<values>`?"
  Approval writes Per machine limits and Per machine owner approval reference in the repo test profile
  in the current change, then reruns Duration-report and Commit commands. Rejection keeps the limit and commit blocked;
  do not ask again for this change. Obtain any scope permission the calling workflow requires before editing the profile.
  Avoid nested worker pools; use one owner for test parallelism.
- Select targeted checks by behaviour and blast radius, using `lsp references` and the repo test profile's affected-T2 selection.
  Targeted checks never replace the pre-commit hook or CI merge gate.
- In the PR report baseline/head counts and fast-tier durations, deltas, units and measurement conditions.
  Explain additions, removals and variation. Mark unavailable comparable evidence unavailable, never zero or passed.
  Count and timing deltas are operational evidence, not statistical claims of behavioural improvement.
