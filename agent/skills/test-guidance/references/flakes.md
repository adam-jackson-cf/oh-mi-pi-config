# Flaky tests

## Objective

Identify, reproduce and remove an intermittent failure's cause with failing-before evidence.

## Guidance

- Establish ownership first: inspect whether the change touches the failing test, its rig,
  dependencies or exercised behaviour.
  An apparently unrelated failure is not automatically caused by the current change.
- For an unrelated failure, follow repo delivery policy instead of silently expanding the task.
  Record the CI run link, test, observed failures out of runs and suspected cause in PR risks.
  Report the classification as provisional and identify the owning follow-up.
  Do not change unrelated files or open an issue without authorization.
  A green re-run does not repair the flake or justify overriding a required check.
- For an owned failure, name the suspected cause before changing code.
  Investigate hidden real clocks, wall-time comparisons, async ordering races, shared caches,
  reused output directories and dependency defects.
- Reproduce before fixing and record failures out of runs, not merely "reproduced".
  Use targeted repeated execution, contention, controlled ordering perturbations or a matching
  CI environment according to the suspected cause.
  Preserve the command, environment, seed and relevant error line.
- If no attempt reproduces the cause, do not make a speculative fix or call it environmental.
  Keep the attempts and CI evidence; report the cause as unknown and classification provisional.
- Write a test that fails on the old implementation before applying the repair.
  Prefer a deterministic injected clock step, forced async ordering or dependency contract check
  over relying on another chance failure.
- Fix the cause: inject time and count fixed steps, explicitly order operations, isolate resources
  per run or correct the dependency through the repo's dependency-change process.
- Never treat a retry, re-run to green, skip, quarantine, longer timeout or loosened assertion
  as a fix.
  A genuinely unavailable tool, host or credential requires concrete evidence; a flaky assertion
  or overloaded timing test is not evidence of unavailable infrastructure.
- Report pre-fix and post-fix failures out of runs with the reproduction conditions in the fix PR.
  Record one evidence-backed cause classification using repo vocabulary.
  Until reproduction supports it, mark the classification provisional.
  Distinguish a test or rig defect from a product or dependency regression, and distinguish both
  from proven unavailable infrastructure.
