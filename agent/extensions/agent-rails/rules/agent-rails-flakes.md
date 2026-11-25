---
agents: [main, task]
repeatMode: once
question: >-
  Is this reply diagnosing, retrying, quarantining or fixing a test that fails only some of
  the time (flaky, intermittent or order-dependent)? Answer no for deterministic failures,
  aborted processes and non-test commands.
condition: ['(?i)\b(?:flak(?:e|y|iness)|intermittent(?:ly)?|non-?determin\w+|sporadic(?:ally)?|(?:fails?|failed) (?:sometimes|occasionally|randomly)|passes? on (?:re-?run|retry))\b']
scope: text
---
# Flaky test

Identify, reproduce and remove the intermittent failure's cause, with failing-before evidence.

Ownership:

- First check whether the change touches the failing test, its rig, its dependencies or the
  behaviour it exercises. An apparently unrelated failure is not automatically caused by the
  change.
- For an unrelated failure, follow the repository's delivery policy instead of silently expanding
  the task. Record the CI run link, the test, the observed failures out of runs and the suspected
  cause in the PR risks. Report the classification as provisional and name the owning follow-up.
  Change no unrelated file and open no issue without authorization.
- A green re-run neither repairs the flake nor justifies overriding a required check.

An owned failure:

- Name the suspected cause before changing code: hidden real clocks, wall-time comparisons, async
  ordering races, shared caches, reused output directories or dependency defects.
- Reproduce before fixing, and record failures out of runs, not just "reproduced". Use targeted
  repeated runs, contention, controlled ordering changes or a matching CI environment, as the
  suspected cause suggests. Keep the command, environment, seed and the relevant error line.
- If no attempt reproduces it, make no speculative fix and do not call it environmental. Keep the
  attempts and the CI evidence, and report the cause as unknown and the classification as
  provisional.
- Write a test that fails on the old implementation before the repair. Prefer a deterministic
  step supplied through the production clock input, controlled async ordering or a dependency
  contract check over waiting for another chance failure.
- Fix the cause: supply time through the production clock input and count fixed steps, order
  operations explicitly, isolate resources per run, or correct the dependency through its own
  change process.
- A retry, re-run to green, skip, quarantine, longer timeout or loosened assertion is never a fix.
  An unavailable tool, host or credential needs concrete evidence; a flaky assertion or an
  overloaded timing test is not evidence of unavailable infrastructure.

Report:

- Give pre-fix and post-fix failures out of runs, with the reproduction conditions, in the fix PR.
- Record one evidence-backed cause classification in the repository's vocabulary, provisional
  until reproduction supports it. Tell a test or rig defect apart from a product or dependency
  regression, and both from proven unavailable infrastructure.
