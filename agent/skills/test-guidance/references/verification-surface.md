# Verification surface

## Objective

Drive usable product outcomes through an isolated, deterministic lever and maintained feature map.

## Guidance

- Read Verification surface in the repo test profile. With a valid no-runnable-app exemption only,
  use outcome tests instead of journeys; do not create an artificial app or divert to setup.
  Use T1 when in-process, T2 seams when the actual host process is needed; never invent a lever for an exempt repo.
- The lever is one command performing launch → doctor → drive → evidence → cleanup.
  Doctor proves ready means usable, not merely that a process or port exists.
- Document the doctor-failure marker in Command notes.
  Classify that marker as blocked infrastructure, never as the expected failing test before a fix,
  even when the command exits nonzero. Stop that run, retain marker/probe evidence, clean up and report blocked
  to the calling workflow. Repair only within authorized scope; otherwise ask the owner once:
  "Repair the environment so doctor succeeds, or leave this test blocked?"
  Resume only after the evidenced cause is repaired and doctor succeeds; rejection leaves the test blocked.
  Never retry merely to get green.
- Accept Data directory argument and Evidence directory argument from the profile.
  Use isolated gitignored or temporary directories. Never drive a shared user instance.
- Clean up after every failed iteration. Verify what dry runs actually skip;
  list stand-in-only setup steps as unverified, never as exercised production setup.
- The lever prints the doctor-failure marker on doctor failure, kills only its own process group,
  uses per-instance ports and data, leaves no processes or ports behind, and keeps the evidence directory after cleanup.

## Feature map and journeys

- Keep one feature-map document per feature with exactly four H2 sections: `Sub-features`,
  `How to get to it (user POV)`, `Driving it with <lever>`, `Gotchas`.
  Put the observable end state in the Sub-features body. Fix every stale link.
  Retain existing file organization when it supports these sections. If the existing map cannot carry them,
  report the layout gap to the calling workflow before writing; do not silently relax headings or create parallel maps.
- Script deterministic T2 journeys. Each journey file matches the profile's Journey file pattern
  and drives the product through the lever command.
  A library's map points to T1 outcome tests when no process, build or UI boundary is crossed.
  Agent exploration belongs in T3, not deterministic journeys.
- Use TDD: demonstrate the missing or broken outcome with a failing test before implementation,
  then make it pass with the product change. Run each journey at least three times;
  count only clean runs, rejecting doctor failures, hangs and contaminated state.
- Run journey files through the test runner.
  During approved setup, write the feature map in the repo and put settings in the repo test profile
  at the location supplied by the calling workflow.
  Hand profile mapping gaps to the workflow that owns setup; separately approved budget-profile
  edits follow [suite-budget.md](suite-budget.md).
- The feature map is the maintained verification source: each feature document links the outcome tests or journeys
  that demonstrate it, with relative Markdown links. Plan a new feature's intended map entry from its requirement
  before writing its test.
- Add a map entry and an outcome test for user-facing behaviour discovered during implementation.
  Show that test failing on the pre-change code and passing after the change; behaviour found late gets no exception.
- Before the PR, list each user-facing behaviour the diff adds or changes, each with a concrete source path;
  an item without one does not count. Any item no changed map document covers gets a map entry and an
  outcome test or journey. The sweep produces map entries and tests, not review findings.
- Run nightly drift only when a feature map and Run events scheduler exist;
  otherwise report the valid surface/scheduler exemption in the PR, or a profile gap if neither is recorded.
