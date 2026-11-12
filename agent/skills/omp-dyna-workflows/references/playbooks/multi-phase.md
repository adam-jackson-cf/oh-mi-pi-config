# Multi-phase playbook

Land independent verified units behind a flag, not long-lived or stacked branches.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation cannot skip its
owner-approved project-setup prerequisite. Helper mechanics live in
[patterns](../patterns.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

Split end-to-end criteria into unit outcomes, dependencies and PRs; declare the flag
removal criterion. A unit waits only on a real dependency.

## 2. Spike

Resolve unanswered cross-unit questions before committing to interfaces.

## 3. Plan

One manifest/slug `<epic>-<unit>` per unit PR. Each unit's proofs fail at base and pass
at head; a unit with no behaviour change freezes none. Name a final flag-removal/cutover
unit with its own end-to-end proofs, not an unverified closing edit.

Give each owner-delegated decision one ADR with a Deciders line naming the
delegation. Mark recommendations revised after delegation as revised and raise
them to the owner before dependent units rely on them. Keep workflow/harness
mechanics out of product ADRs.

Assign a shared status file (e.g. `HANDOFF.md`) one owning unit per wave; other
units report status in their PR bodies.

## 4. Implement

Run shared stage 4 for every unit with a separate implementation task. Main
coordinates independent units; the final unit writer removes the flag and obsolete paths.

## 5. Verify

Each unit, including final flag removal, gets its own prove and exact-head CI.
Final proofs cover combined end-to-end behaviour.
Land decision/ADR units before proving their dependent units.

## 6. Integrate

Integrate units in dependency order; no long-lived or stacked branches.
