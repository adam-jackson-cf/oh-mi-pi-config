# Suite budget

## Objective

Keep test growth justified, assertions singly owned and verification within approved machine budgets.

## Guidance

- Apply the repo test profile's Fast-tier budget, Tiers, Ownership areas and Command notes.
  This guidance adds no runner or numeric cap; fix policy violations rather than weaken existing enforcement.

### Justified growth

- Give each new test a behaviour name stating the caller-visible outcome it demonstrates.
  Check that the stated behaviour matches its assertions; rewrite or remove vacuous cases.
- Prefer extending an existing case.
  A new file, internal branch or implementation detail is not a new behaviour.
- Rewrite or delete tests that pass when their claimed behaviour breaks or fail only on harmless refactors,
  preserving real coverage first. Add no speculative cases outside the change's demonstrated blast radius.

### Duplicate paths

- Apply [tiers.md](tiers.md) Assertion ownership; keep exact caller values at the public entry point
  or in pure-core properties, never internal decision tests. Shared setup does not justify duplicate assertions.

### Placement

- Place tests by what they drive, following [tiers.md](tiers.md) and Layout and naming.
- Drive public entry points with deterministic production inputs; use property tests only for pure cores.
  Never extract internals merely for coverage. Never relabel a slow test to evade a cap.

### Per-test time

- Budgets apply only at the agent's commit; CI never measures or reports test timing.
- When the checker reports an unknown machine, ask the owner once to record a baseline;
  approval permits recording the measured limit and reference; rejection leaves timing report-only.
  The first limit and every raise require owner approval; record its reference per machine.
  Approval = the owner's decision, documented in the repo test profile;
  write an approval field only as the owner stated it.
- Fix excess cost: remove real-clock waits, bound the actual waiting object, isolate contention and minimize steps.
  Keep one scenario per test; never split redundant work merely to disguise its total cost.

### Fast-tier time

- Compare baseline and head under comparable command, cache, runner and environment conditions.
- Remove redundant coverage, unnecessary work and wrong-tier execution before adding cost.
  Stop cutting when no further redundant or misplaced work is identifiable; never delete required coverage.
  If still over budget, report the commit blocked and ask the repo owner through the calling workflow once:
  "Approve a fast-tier wall limit of `<value>` on `<machine id>`, based on these repeated measurements: `<values>`?"
  On approval, the owner writes Per machine limits and Per machine owner approval reference in the repo test profile
  by hand; then rerun Duration-report and Commit commands. Rejection keeps the limit and commit blocked;
  do not ask again for this change.
  Avoid nested worker pools; use one owner for test parallelism.
- Select targeted checks by behaviour and blast radius, using `lsp references` and the repo test profile's affected-T2 selection.
  Targeted checks never replace the commit checks or CI merge gate.
- In the PR report baseline/head counts and fast-tier durations, deltas, units and measurement conditions.
  Explain additions, removals and variation. Mark unavailable comparable evidence unavailable, never zero or passed.
  Count and timing deltas are operational evidence, not statistical claims of behavioural improvement.
