# Suite budget

## Objective

Keep test growth justified, assertions singly owned and verification within repo-owned time budgets.

## Guidance

- Apply the repo's existing budgets, placement checks and reporting format.
  This guidance adds no runner, numeric cap or parallel gate and removes no existing enforcement.
  Fix violations of existing policy instead of weakening that policy.

### TEST.growth.justified

- Map every new test to a caller-visible behaviour that the change adds or alters.
  Name that behaviour in the test and explain its coverage gap in the PR body.
- Prefer tightening or extending an existing test over adding another test or scenario.
  A new file, internal branch or implementation detail is not itself a new behaviour.
- Delete tests that still pass when their claimed behaviour breaks, after preserving any real
  behavioural coverage they own.
  Replace or delete assertions that fail only because internals change while public behaviour holds.
- Add no speculative cases unrelated to the change's behaviour or demonstrated blast radius.

### TEST.duplicate-path

- Give every assertion one owner across tiers.
  Keep the exact decision assertion in T1 and only the real-boundary contract at the boundary tier.
  Do not repeat decision cases in journeys to increase apparent confidence.
- Move fixtures with their owning test and remove the superseded copy.
  Shared setup is not an excuse to maintain duplicate assertions over the same path.

### TEST.placement

- Place a test by what it drives, following [tiers.md](tiers.md) and the repo's owning-package layout.
  Keep real-boundary tests and inherently slow verification out of the fast tier.
- Extract pure decisions and inject clocks or runners so decision coverage stays in memory.
  Do not relabel an accidentally slow unit test as boundary or scheduled coverage to evade a cap.

### TEST.budget.per-test-time

- Measure changed tests with the repo runner's duration report against the repo's per-test cap.
  Account for the supported CI environment rather than only a fast local machine.
- Fix an over-cap test instead of raising the cap or timeout: remove real-clock waits, bound the
  waiting object, isolate resource contention and use the minimum fixed step count needed.
- Keep one scenario per test and split genuinely distinct scenarios into separate named cases.
  Do not split redundant work merely to conceal an unchanged total verification cost.

### TEST.budget.fast-tier-time

- Measure the repo's fast tier (`checks.commit` in `.omp/gate.json`) under comparable baseline and changed
  conditions, using the same command, runner, cache state and environment.
  Follow repo timing policy when repeated measurements or a particular summary are required.
- Keep the aggregate fast-tier duration within the repo's cap.
  Remove redundant coverage or unnecessary work and correct tier placement before adding cost.
- Select targeted checks by changed behaviour and blast radius, using codegraph impact when
  available, rather than repeatedly running the whole suite.
  Targeted checks do not replace the pre-commit hook's `checks.commit` or CI's `checks.ci`.
- In the PR body, report baseline and changed test counts, their delta, baseline and changed
  fast-tier duration, its delta, units and measurement conditions.
  Explain additions, removals and any timing variation; report unavailable evidence as unavailable,
  never as zero cost or a passed budget.
