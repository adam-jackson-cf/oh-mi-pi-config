# Feature playbook

Deliver the requested feature with frozen acceptance evidence.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation cannot skip its
owner-approved project-setup prerequisite. Helper mechanics live in
[patterns](../patterns.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

Define the new user-visible outcome and affected owners/callers.

## 2. Spike

Resolve only remaining questions on the real feature surface.

## 3. Plan

Use `new-surface` for absent APIs and `preserve` for existing behaviour;
use `defect` with asserted `expectRed` for regressions. Lever journeys are only
`defect` or `preserve`, never `new-surface`; reject doctor/environment failures.
Proof tasks freeze runner-invoked journey files
and required lever changes together. Implementation changes product code and map docs only.

## 4. Implement

Implement the feature within the framed scope.

## 5. Verify

Exercise the feature on its real surface under the shared risk tier.

## 6. Integrate

No feature-specific integration delta.
