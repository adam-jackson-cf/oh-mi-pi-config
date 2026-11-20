# Bug fix playbook

## Objective

Repair the reproduced failure class, not merely the reported value.

## Stage deltas

- Frame: record actual versus expected behaviour and representative failure-class criteria.
  Read [delivery principles](delivery-principles.md).
- Spike: reproduction is mandatory. For intermittent failures read `skill://test-guidance/references/flakes.md`;
  control causes, never suppress evidence.
- Plan: read [proofs](proofs.md) and `skill://test-guidance/references/case-selection.md`.
- Plan: each proof reproduces the failure and is linked from the affected feature-map entry.
- Plan: extend an existing scenario only in a small dedicated file; otherwise add a dedicated proof file and list every
  needed shared fixture. Whole files are frozen.
- Implement: the implementation task repairs the root cause, not a special case of the reported input.
- Verify: proofs cover representative members of the failure class, not a value pin.
