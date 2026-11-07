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

Use `preserve` for unchanged behaviour and `new-surface` for new APIs/design surfaces.
A red preserve baseline returns to Plan to repair proof/objective, never to weaken it.

## 4. Implement

Migrate callers/tests/docs and remove obsolete implementations, aliases, comments
and compatibility paths.

## 5. Verify

Exercise preserved behaviour and any new design surface using their chosen proof kinds.

## 6. Integrate

No refactor-specific integration delta.
