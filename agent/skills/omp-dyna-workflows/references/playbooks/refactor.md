# Refactor playbook

Make the target design canonical, migrate every caller and preserve required behaviour.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation cannot skip its
owner-approved project-setup prerequisite. Helper mechanics live in
[patterns](../patterns.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

Inventory callers and preserved behaviours. Read `skill://coding-guidance/references/clean-cutovers.md`.

## 2. Spike

Resolve design uncertainty only when inspection cannot settle it.

## 3. Plan

A behaviour-preserving refactor maps no proofs: freeze an empty manifest, and `prove()`
checks CI green only. The repo's existing tests and CI carry the preserved behaviour.
`prove()` blocks any feature-map doc changed without a linked proof, so a refactor that moves
a mapped file and must edit a map link is not zero-proof: raise it with the owner in Plan.
If the refactor adds behaviour, that part is a feature: its proofs fail at base, pass at head
and are linked from their feature-map entries; implementation changes product code and map docs,
plus a discovery's map entry and outcome test before a re-freeze; never frozen proof bytes.

## 4. Implement

Migrate callers/tests/docs and remove obsolete implementations, aliases, comments
and compatibility paths.

## 5. Verify

CI green on the exact head, plus any frozen proofs for added behaviour.

## 6. Integrate

No refactor-specific integration delta.
