---
name: "setup-repo-proofs"
description: "USE WHEN establishing or extending enforceable repository guardrails for a selected language and runtime stack."
hide: true
disable-model-invocation: true
---

# Workflow

Establish repository-owned controls using the project's selected rule contract and language/runtime
stack. Reuse existing tools, commands and ownership policies; add missing rules to those controls
rather than installing a second authority. Keep local checks and CI runnable by any contributor.
Every applicability or coverage claim needs source evidence for the individual rule.

## Step 0: Set target and safety boundary

Identify the Git checkout, base ref, protected work, stack and setup modes. Keep analysis and
candidate changes outside the target until approval.
[Target and safety boundary](references/step-0-target-and-safety-workflow.md).

## Step 1: Inventory existing controls

Distinguish active checks, instructions, user documentation and developer practice. Map every rule,
lock file and manifest to its enforcing source line or an explicit gap.
[Existing guardrail inventory](references/step-1-inventory-workflow.md).

## Step 2: Map the complete rule contract

Map every selected rule ID, semantic and threshold to an implementation, evidence-backed conditional
exclusion or blocker. Never substitute a tool name for demonstrated enforcement.
[Rule contract mapping](references/step-2-rule-mapping-workflow.md).

## Step 3: Prepare candidate changes

Prepare controls, tests and fixes in a temporary checkout. Preserve project commands and make local
and CI failure propagation explicit.
[Candidate implementation](references/step-3-candidate-implementation-workflow.md).

## Step 4: Measure the dry-run impact

Report whole-repository findings, candidate file and line changes, measured costs and unknowns.
[Dry-run impact](references/step-4-dry-run-workflow.md).

## Step 5: Approve changes

Present the action matrix and complete rule mapping. **STOP** until the owner approves the exact
paths, rules and merge/replace decisions.
[Change approval](references/step-5-approval-workflow.md).

## Step 6: Apply approved changes

Apply only approved changes in a reversible checkout and deliver repository changes through the
normal authorized PR process.
[Approved application](references/step-6-apply-workflow.md).

## Step 7: Verify every applicable rule

Demonstrate effective checks, accepted valid inputs, rejected violations and local/CI parity.
[Rule verification](references/step-7-verify-workflow.md).

## Result format

Return these sections in the terminal; do not save a retained summary artifact:

- **Target and audience:** repository, stack, base ref, CI provider and required status, exclusions,
  protected paths and setup modes.
- **Existing controls:** active versus manual/proposed instructions, checks, tests and documentation,
  their owners, source lines and triggers.
- **Rule coverage:** every selected ID, implementation, conditional exclusion with evidence or blocker;
  every lock file and manifest's audit source or required fix.
- **Proposed actions:** keep/merge/replace/add/defer, paths, rule IDs and verification.
- **Dry-run impact:** whole-repository findings by rule/path, file and line counts, measured costs,
  estimates and unknowns.
- **Decisions:** explicit owner choices or approval still pending.
- **Verification:** commands/results, local/CI parity, documentation changes and unresolved gaps.

Keep candidate files temporary until approval. A narrower selection changes enabled rules, never
the whole-repository scope of a retained rule. A blocker is never a pass.
