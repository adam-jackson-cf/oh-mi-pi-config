# Suite budget

## Objective

Keep test growth justified, assertions singly owned and verification within approved machine budgets.

## Guidance

- Apply the repo test profile's Fast-tier budget, Tiers, Ownership areas and Command notes.
  This guidance adds no runner or numeric cap; fix policy violations rather than weaken existing enforcement.

### Justified growth

- Give each new test a behaviour name stating the caller-visible outcome it demonstrates.
  A behaviour name has at least two meaningful words besides filler such as test, case, todo, tbd,
  placeholder or example; one name covers all its parameter rows.
  When reviewing a change set, sample up to 20 new test names and ask of each: does the named behaviour
  match what the test asserts? If more than 2 are vacuous, review every new test name and rename,
  rewrite or remove each mismatch.
- Prefer extending an existing case.
  A new file, internal branch or implementation detail is not a new behaviour.
- Rewrite or delete tests that pass when their claimed behaviour breaks or fail only on harmless refactors,
  preserving real coverage first. Add no speculative cases outside the change's demonstrated blast radius.

### Duplicate paths

- Apply [tiers.md](tiers.md) Assertion ownership; keep exact caller values at the public entry point
  or in pure-core properties, never internal decision tests. Shared setup does not justify duplicate assertions.
- A new test duplicates another when it drives the same normalized call path and makes the same
  normalized assertion; merge or remove it.

### Placement

- Place tests by what they drive, following [tiers.md](tiers.md) and Layout and naming.
  Map every test to its declared owner and tier, including T2 ownership areas; reject unmapped or
  misplaced tests. Keep fixtures and support under the profile's layout directories.
- Drive public entry points with deterministic production inputs; use property tests only for pure cores.
  Never extract internals merely for coverage. Never relabel a slow test to evade a cap.

### Per-test time

- Run timing budgets locally at commit; CI does not measure or report test timing.
- Derive every first limit and every proposed raise, per-test and whole-tier, from at least five repeated
  local runs on the same machine under comparable conditions, never from one convenient run.
  Include the measurements in the approval request.
- If no baseline is recorded for this machine, ask the owner once to record a baseline;
  approval permits recording the measured limit and reference; rejection leaves timing report-only.
  The first limit and every raise require owner approval; record its reference per machine.
  Approval = the owner's decision, documented in the repo test profile;
  write an approval field only as the owner stated it.
- Fix excess cost: remove real-clock waits, bound the actual waiting object, isolate contention and minimize steps.
  Keep one scenario per test; never split redundant work merely to disguise its total cost.

### Fast-tier time

- Compare baseline and head under comparable command, cache, runner and environment conditions.
- Derive whole-tier first limits and raises from at least five repeated local runs on the same machine
  under comparable conditions, including the measurements in the owner approval request.
- Remove redundant coverage, unnecessary work and wrong-tier execution before adding cost.
  Stop cutting when no further redundant or misplaced work is identifiable; never delete required coverage.
  If still over budget, report the commit blocked and ask the repo owner through the calling workflow once:
  "Approve a fast-tier wall limit of `<value>` on `<machine id>`, based on these repeated measurements: `<values>`?"
  After owner approval, update the limits and approval reference through the repo's authorised
  configuration process; rerun the Duration-report and Commit commands. Rejection keeps the limit and commit blocked;
  do not ask again for this change.
  Avoid nested worker pools; use one owner for test parallelism.
- Select targeted checks by behaviour and blast radius, using `lsp references` and the repo test profile's affected-T2 selection.
  Targeted checks never replace the commit checks or CI merge gate.
- In the PR report baseline/head counts and fast-tier durations, deltas, units and measurement conditions.
  Explain additions, removals and variation. Mark unavailable comparable evidence unavailable, never zero or passed.
  Count and timing deltas are operational evidence, not statistical claims of behavioural improvement.
