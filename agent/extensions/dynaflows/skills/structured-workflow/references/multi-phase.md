# Multi-phase playbook

## Objective

Land independent verified units behind a flag, not long-lived or stacked branches.

## Stage deltas

- Frame: split end-to-end criteria into unit outcomes, dependencies and PRs, and declare the flag removal criterion.
  A unit waits only on a real dependency.
- Spike: resolve unanswered cross-unit questions before committing to interfaces.
- Plan: use one manifest and slug `<epic>-<unit>` per unit PR; a unit with no behaviour change freezes no proofs.
- Plan: name a final flag-removal or cutover unit with its own end-to-end proofs, not an unverified closing edit.
- Plan: mark recommendations revised after the owner delegated a decision as revised, and raise them to the owner
  before dependent units rely on them.
- Plan: assign a shared status file (e.g. `STATUS.md`; `HANDOFF.md` is the uncommitted session handoff) one owning unit per wave; other units report status in
  their PR bodies.
- Implement: every unit completes its proof task and main's freeze before its own implementation task.
  Main coordinates independent units; the final unit's writer removes the flag and obsolete paths.
- Verify: every unit, including the final flag removal, is verified on its own exact PR head.
  Final proofs cover the combined end-to-end behaviour. Land the units others depend on before proving them.
- Integrate: integrate units in dependency order, each under the full merge gate.
