# Suite budget

## Objective

Keep test growth justified, assertions singly owned and verification within approved machine budgets.

## Guidance

- Apply the repo test profile's Fast-tier budget, Tiers, Ownership areas and Command notes.
  This guidance adds no runner or numeric cap; fix policy violations rather than weaken existing enforcement.
  TEST.* headings name the guardrail rule IDs; retain them.

### TEST.growth.justified

- Map each new test to an added or altered caller-visible behaviour and explain the coverage gap in the PR.
- Prefer extending an existing case, except when DynaFlows proof freezing requires a dedicated file.
  A new file, internal branch or implementation detail is not a new behaviour.
- Rewrite or delete tests that pass when their claimed behaviour breaks or fail only on harmless refactors,
  preserving real coverage first. Add no speculative cases outside the change's demonstrated blast radius.

### TEST.duplicate-path

- Apply [tiers.md](tiers.md) Assertion ownership; keep exact caller values at the public entry point
  or in pure-core properties, never internal decision tests. Shared setup does not justify duplicate assertions.

### TEST.placement

- Place tests by what they drive, following [tiers.md](tiers.md) and Layout and naming.
  Keep processes and network out of T1 across setup, call and teardown.
- Drive public entry points with deterministic production inputs; use property tests only for pure cores.
  Never extract internals or add test-only clock/runner hooks merely for coverage.
  Never relabel a slow test to evade a cap.

### TEST.budget.per-test-time

- Measure changed tests with Duration-report command. Select approved Per machine limits by the measuring machine,
  recording its platform and runtime. Per-test limits block locally in the commit command on that machine.
- CI runs the same checker report-only for timing, retaining its local/CI evidence marker.
  Missing, invalid or stale duration reports are fatal everywhere, including report-only mode.
  Apply the repo checker's validity/freshness rules recorded in Command notes; if absent, report a policy gap.
- An unapproved or unknown machine runs timing report-only. Ask the owner once to record a baseline;
  approval permits recording the measured limit and reference; rejection leaves timing report-only.
  The first limit and every raise require owner approval; record its reference per machine.
  Derive proposed limits from repeated local measurements, never from a convenient single run.
- Fix excess cost: remove real-clock waits, bound the actual waiting object, isolate contention and minimize steps.
  Keep one scenario per test; never split redundant work merely to disguise its total cost.

### TEST.budget.fast-tier-time

- Measure fast-tier wall time from Duration-report command under comparable baseline/head command,
  cache, runner and environment conditions.
  Exceeding the measuring machine's approved wall limit fails the local commit command; CI timing is report-only.
  The same report-validity and owner-approval rules above apply to the wall limit.
- Remove redundant coverage, unnecessary work and wrong-tier execution before adding cost.
  Stop cutting when no further redundant or misplaced work is identifiable; never delete required coverage.
  If still over budget, report the commit blocked and ask the repo owner through the calling workflow once:
  "Approve a fast-tier wall limit of `<value>` on `<machine id>`, based on these repeated measurements: `<values>`?"
  Approval writes Per machine limits and Per machine owner approval reference in the repo test profile
  in the current change, then reruns Duration-report and Commit commands. Rejection keeps the limit and commit blocked;
  do not ask again for this change. Obtain any scope permission the calling workflow requires before editing the profile.
  Avoid nested worker pools; use one owner for test parallelism.
- Select targeted checks by behaviour and blast radius, using codegraph when available.
  Targeted checks never replace the pre-commit hook or CI merge gate.
- In the PR report baseline/head counts and fast-tier durations, deltas, units and measurement conditions.
  Explain additions, removals and variation. Mark unavailable comparable evidence unavailable, never zero or passed.
  Count and timing deltas are operational evidence, not statistical claims of behavioural improvement.
