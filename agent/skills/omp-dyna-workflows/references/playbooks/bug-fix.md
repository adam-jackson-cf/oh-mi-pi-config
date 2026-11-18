# Bug fix playbook

Repair the reproduced failure class, not merely the reported value.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation requires a valid pathCheck-free
schema-v2 record with `checks.commit` and a complete path-check profile; missing controls
require owner-approved Project setup. Helper mechanics live in [patterns](../patterns.md)
and [verify-loop](../verify-loop.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

Record actual versus expected behaviour and representative failure-class criteria. Read `skill://coding-guidance/references/regression-coverage.md`.

## 2. Spike

Reproduction is mandatory. For intermittent failures read
`skill://test-guidance/references/flakes.md`; control causes, never suppress evidence.

## 3. Plan

Each proof reproduces the failure: it fails at base and passes at head. Extend an existing
scenario only in a small dedicated file; otherwise add a dedicated proof file and list every
needed shared fixture. Whole files are frozen.
Use outcome tests or lever journeys linked from the affected feature-map entry; a
doctor/environment failure is not red.
Freeze runner-invoked journey files
with needed lever changes; implementation changes product code and feature-map docs,
plus a discovery's map entry and outcome test before a re-freeze; never frozen proof bytes.

## 4. Implement

Complete the proof task and main's red-at-base freeze before the separate implementation task
repairs the root cause, not a special case of the reported input. Main never commits.

## 5. Verify

Proofs cover representative members of the failure class, not a value pin;
main runs `prove()` and then `structure_review()` on the exact PR head.

## 6. Integrate

The stage-4 record remains required. Merge only with exact-head CI green, prove `status: pass`
and structure `halt: false`; missing, invalid or wrong-head results block. Rerun both helpers
after any head change. Main never commits.
