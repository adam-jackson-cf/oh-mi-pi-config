# Flaky tests

Read this when a test passes and fails on the same tree, locally or in CI.

## Scope

- When fixing the flake is the task, go to Reproduce. Otherwise decide whether the current change touches the failing test,
  its rig, its dependencies or the behaviour it exercises; an apparently unrelated failure is neither automatically caused
  by the change nor automatically unrelated.
- For an unrelated failure, report the test, the failures out of runs and the suspected cause, and change no unrelated file.
- A green re-run does not repair a flake.

## Reproduce

- Name the suspected cause before changing code: hidden real clocks, wall-time comparisons, async ordering races, shared caches,
  reused output directories or dependency defects.
- Reproduce before fixing, and record failures out of runs (for example 7/200), not merely "reproduced".
  Choose repeated runs, contention, controlled ordering changes or a CI-like environment to suit the suspected cause.
  Keep the command, the environment, the seed when one applies, and the relevant error line.
- If no attempt reproduces it, make no speculative fix; report the attempts and the cause as unknown.

## Fix

- When the repair adds the hidden input itself (a clock, an ordering control or a resource the test could not reach),
  no new test can run on the old code. The reproduction's failures out of runs are then the failing-before evidence;
  the new test's type or signature error on the old code is not.
- Fix the cause: count fixed steps with time supplied as the skill's production inputs line states, order operations explicitly,
  isolate resources per run, or correct the dependency through its own change process.
- Calling a tool, host or credential unavailable needs concrete evidence; a flaky assertion or an overloaded timing test is not that evidence.

## Report

- Report failures out of runs before and after the fix, the reproduction conditions and the cause.
