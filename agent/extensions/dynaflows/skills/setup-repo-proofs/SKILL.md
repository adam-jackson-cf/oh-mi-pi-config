---
name: "setup-repo-proofs"
description: "Use when establishing or extending enforceable repository guardrails for a selected language and runtime stack."
hide: true
disable-model-invocation: true
---

# Workflow

## Step 0: Set target and safety boundary

- **Purpose**: Identify the Git checkout, base ref, protected work, stack and setup modes.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Workflow: [references/step-0-target-and-safety-workflow.md](references/step-0-target-and-safety-workflow.md)

## Step 1: Inventory existing controls

- **Purpose**: Separate active checks from instructions and documentation, and map every rule, lock and manifest to evidence.
- Workflow: [references/step-1-inventory-workflow.md](references/step-1-inventory-workflow.md)

## Step 2: Map the complete rule contract

- **Purpose**: Map every selected rule ID to an implementation, evidence-backed conditional exclusion or blocker.
- Workflow: [references/step-2-rule-mapping-workflow.md](references/step-2-rule-mapping-workflow.md)

## Step 3: Prepare candidate changes

- **Purpose**: Prepare controls, tests and fixes in a temporary checkout outside the target.
- Workflow: [references/step-3-candidate-implementation-workflow.md](references/step-3-candidate-implementation-workflow.md)

## Step 4: Measure the dry-run impact

- **Purpose**: Report whole-repository findings, candidate changes, measured costs and unknowns before any target edit.
- Workflow: [references/step-4-dry-run-workflow.md](references/step-4-dry-run-workflow.md)

## Step 5: Approve changes

- **Purpose**: Make the owner's explicit decision the boundary before any target write.
- **STOP**: Gates every target write on the owner's approval of exact paths, rules and merge/replace decisions.
- Workflow: [references/step-5-approval-workflow.md](references/step-5-approval-workflow.md)

## Step 6: Apply approved changes

- **Purpose**: Land only approved changes through the normal authorized PR process, preserving unrelated work.
- Workflow: [references/step-6-apply-workflow.md](references/step-6-apply-workflow.md)

## Step 7: Verify every applicable rule

- **Purpose**: Demonstrate effective checks, accepted valid inputs, rejected violations and local/CI parity.
- Workflow: [references/step-7-verify-workflow.md](references/step-7-verify-workflow.md)

## Output

### Result Format

Return these sections in the terminal; do not save a retained summary artifact:

- **Target and audience:** repository, stack, base ref, CI provider and required status, exclusions, protected paths and modes.
- **Existing controls:** active versus manual/proposed instructions, checks, tests and documentation, owners, sources, triggers.
- **Rule coverage:** every selected ID with its implementation, evidenced conditional exclusion or blocker;
  every lock file and manifest's audit source or required fix.
- **Proposed actions:** keep/merge/replace/add/defer, paths, rule IDs and verification.
- **Dry-run impact:** whole-repository findings by rule/path, file and line counts, measured costs, estimates and unknowns.
- **Decisions:** explicit owner choices or approval still pending.
- **Verification:** commands/results, local/CI parity, documentation changes and unresolved gaps.
