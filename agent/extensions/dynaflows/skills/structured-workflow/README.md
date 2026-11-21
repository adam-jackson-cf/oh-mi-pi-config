# structured-workflow

## Overview

Structured-workflow is the six-stage delivery workflow the harness runs for a repository `modes.json` sets to structured:
Frame, Spike, Plan, Implement, Verify and Integrate, after a preflight that resolves the mode, the record and one
playbook. Proofs are outcome tests frozen red at base and proved green at the PR head; merge needs exact-head CI,
`prove()` and `structure_review()`. The [design directive](../../AGENTS.md) holds the principles behind it, and
[workflow.html](../../docs/workflow.html) explains it for people.

## When to use it

- The owner asks for a feature, bug fix, refactor, investigation or multi-PR delivery in a repo in dynaflows structured mode.
- The owner asks for structured mode in a repo that has no record yet (harness setup).
- Orchestrated fan-out, calibrated judgments or blind human labels are requested.

## Example prompts

- "$dynaflows structured" in a repo without a record, then "fix the login redirect bug".
- "Add an export option to the report command, with proofs."
- "Investigate why the nightly job is slow; evidence only."

## References

- Playbooks hold only their deltas to the stage references; the shared stage text lives once in `references/step-*`.
- Harness setup is a set of deltas to `skill://setup-repo-proofs`, one reference per step that has deltas, so the generic
  workflow stays usable without the harness.
- Some structure and naming rules are tagged "Guidance only" because no deterministic check or calibrated judgment can
  decide them yet:
  - inferring architectural tiers from an arbitrary dependency graph, or choosing a repo's native import checker,
    needs toolchain evidence;
  - whether an unchanged test still exercises moved behaviour needs execution and semantic judgment
    (`RENAME.residue` catches retired names);
  - which data is a measured, frozen version, and where the current-version pointer lives, needs provenance;
  - runtime loaders, import search and fallback locations need language-aware analysis beyond configured import edges;
  - layout equivalence between instances, the closest semantic home for brownfield code, concept boundaries,
    behavioural equivalence of moves and obsolete scaffolding all need owner or lifecycle context.
