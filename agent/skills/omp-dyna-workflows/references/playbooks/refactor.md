# Refactor playbook

Make the target design canonical, migrate every caller and preserve required behaviour.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation requires a valid pathCheck-free
schema-v2 record with `checks.commit` and a complete path-check profile; missing controls
require owner-approved Project setup. Helper mechanics live in [patterns](../patterns.md)
and [verify-loop](../verify-loop.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

Inventory callers and preserved behaviours. Read `skill://coding-guidance/references/clean-cutovers.md`.

## 2. Spike

Resolve design uncertainty only when inspection cannot settle it.

## 3. Plan

A behaviour-preserving refactor maps no proofs: freeze an empty manifest, and `prove()`
checks CI green and rename residue. The repo's existing tests and CI carry the preserved behaviour.
`prove()` blocks any feature-map doc changed without a linked proof, so a refactor that moves
a mapped file and must edit a map link is not zero-proof: raise it with the owner in Plan.
If the refactor adds behaviour, that part is a feature: its proofs fail at base, pass at head
and are linked from their feature-map entries; implementation changes product code and map docs,
plus a discovery's map entry and outcome test before a re-freeze; never frozen proof bytes.

## 4. Implement

Main freezes the empty manifest, or completes the proof task and red-at-base freeze for added
behaviour, before the separate implementation task migrates callers/tests/docs and removes
obsolete implementations, aliases, comments and compatibility paths. Main never commits.

## 5. Verify

With the stage-4 record still valid, main runs `prove()` and then `structure_review()` on the
exact PR head, even with an empty manifest; CI is green on that head.

## 6. Integrate

Merge only with the valid stage-4 record, exact-head CI green, prove `status: pass` and structure
`halt: false`; missing, invalid or wrong-head results block. Rerun both helpers after any head
change. Main never commits.
