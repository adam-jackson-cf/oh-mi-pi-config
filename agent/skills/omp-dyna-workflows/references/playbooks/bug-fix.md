# Bug fix playbook

Repair the reproduced failure class, not merely the reported value.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation cannot skip its
owner-approved project-setup prerequisite. Helper mechanics live in
[patterns](../patterns.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

Record actual versus expected behaviour and representative failure-class criteria. Read `skill://coding-guidance/references/regression-coverage.md`.

## 2. Spike

Reproduction is mandatory. For intermittent failures read
`skill://test-guidance/references/flakes.md`; control causes, never suppress evidence.

## 3. Plan

Use `defect` proofs with specific asserted `expectRed`. Extend an existing scenario
only in a small dedicated file; otherwise add a dedicated proof file and list every
needed shared fixture. Whole files are frozen.
Use outcome tests or lever journeys; journeys are `defect` or `preserve`, never
`new-surface`. A doctor/environment marker cannot be `expectRed`.
Run journey proofs several times before freeze. Freeze runner-invoked journey files
with needed lever changes; implementation changes product code and feature-map docs only.

## 4. Implement

Repair the root cause, not a special case of the reported input.

## 5. Verify

Exercise representative members of the failure class, not a value pin.

## 6. Integrate

No bug-specific integration delta.
