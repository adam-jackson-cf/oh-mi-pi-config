# setup-repo-proofs

## Overview

Set up enforceable verification controls in a Git repository and prove each one works.
The workflow inventories what the repository already enforces, maps every rule in a bundled catalogue of 164 rules to the target stack,
drafts runnable checks outside the repository, and measures their impact as a dry run.
It changes nothing until the owner approves a rollout scope, a tier and exact paths, and it finishes by proving every applicable rule
with the validator, positive and negative canaries, and the real enforcement points.
A rule counts as enforced only when a deterministic point (an installed hook, a CI status host protection requires, or a host
setting) runs it, blocks on failure, and a canary proves it; agent instructions and documentation never count.

Requirements: Git; Python 3.11+ (standard library only) for the bundled scripts; the GitHub CLI (`gh`) is optional and used to read
required checks and branch protection. Each script runs `--self-test` without network access.
The target's own tools (Ruff, mypy, tsc, oxlint, golangci-lint, ShellCheck, shfmt, its test runner) are installed through the target's
package manager only after approval.

### How it decides

- **Verification layers.** The catalogue covers gate wiring, structure, naming, code integrity, test evidence, verification surface,
  supply chain, and the Python, TypeScript, Go and shell tool families.
- **The repository owns every control.** Checkers, tool configs, hooks, CI jobs, `proofs.toml` and the validator live in the
  target repository, so anyone can run them without this skill. Each value has one home: its native config, or `proofs.toml` when
  it has none. Setup run state stays in the excluded `.proofs/` folder, which no check reads.
- **Grow, don't duplicate.** Each rule family gets one outcome: `keep` an existing checker, `grow` it with the missing rules, or `add`
  a new control. A second copy of a rule never runs beside the owning checker.
- **Scope without baselines.** The owner chooses a whole-repository, changed-code (against a base ref) or selected-paths rollout.
  Scope narrows which files are gated, never which rules apply; whole-repository findings stay visible, and waived-finding files are never accepted.
- **Opinionated defaults.** Complexity 10, five arguments, 50 statements, fan-out 10, strict typing, no comments or placeholder bodies
  in added code, behaviour-named tests, per-machine test budgets, and every claim's CI status required on the exact head.

### Bundled assets and scripts

| Path | Purpose |
| --- | --- |
| `assets/guardrail-rules.json` | The rule catalogue: IDs, contracts, applicability and evidence kinds |
| `assets/templates/proofs.toml` | The target's claims: control, enforcement points, canary, approved exclusions and settings with no native home |
| `assets/conventions/` | Language and tooling packs and house style for naming and structure |
| `assets/python-reference/` | Adaptable Python checkers for the `PYQ`, `PYS` and `FANOUT` rules |
| `assets/conformance-cases.json` | Framework-neutral cases a new test-framework adapter must pass |
| `scripts/inventory_controls.py` | Read-only inventory of commands, hooks, CI jobs, required checks, tool configs, locks and manifests |
| `scripts/propose_tool_configs.py` | Renders the catalogue's tool settings as missing native-tool configs, outside the target |
| `scripts/list_scope.py` | Lists the files a whole, changed or selected-paths scope covers |
| `scripts/validate_rule_coverage.py` | Fails unless every `proofs.toml` claim is enforced and proven; with `--catalog`, also every catalogue rule |

## When to use it

- A new Git repository needs a developer verification baseline.
- An existing repository needs full rule coverage without replacing its working lint, test or CI practice.
- A team wants to compare whole-repository adoption with a changed-code or selected-path rollout before committing to it.
- Application-user guidance and contributor guidance need distinct owners.

## Example prompts

- "Use `setup-repo-proofs` to assess this Python repository and show the complete dry run before editing."
- "Use `setup-repo-proofs` to set up verification for this TypeScript repository, gating only new changes against `main` at first."
- "Use `setup-repo-proofs` to merge equivalent checks into our existing CI and show which rules cannot be enforced yet."

## References

- [Step 0: Target and safety boundary](references/step-0-target-and-safety-workflow.md)
- [Step 1: Existing control inventory](references/step-1-inventory-workflow.md)
- [Step 2: Rule contract mapping](references/step-2-rule-mapping-workflow.md)
- [Step 3: Candidate implementation](references/step-3-candidate-implementation-workflow.md)
- [Step 4: Dry-run impact](references/step-4-dry-run-workflow.md)
- [Step 5: Change approval](references/step-5-approval-workflow.md)
- [Step 6: Approved application](references/step-6-apply-workflow.md)
- [Step 7: Rule verification](references/step-7-verify-workflow.md)
- [Design principles](references/design-principles.md), [verification layers](references/verification-layers.md),
  [tooling families](references/tooling-families.md), [structure and naming](references/structure-and-naming.md),
  [test evidence](references/test-evidence.md), [dependency audit](references/dependency-audit.md), [gate wiring](references/gate-wiring.md),
  [run state](references/run-state.md)
