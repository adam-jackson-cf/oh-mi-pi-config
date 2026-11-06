---
name: "setup-repository-guardrails"
description: "USE WHEN establishing or extending enforceable repository guardrails for a selected language and runtime stack."
hide: true
disable-model-invocation: true
---

# Workflow

## Step 0: Set target and safety boundary

- Purpose: Establish the Git checkout, chosen stack, adoption scope, and protected work.
- Action: Check repository state and resolve inputs before reading or writing target files.
- Workflow: [Target and safety boundary](references/step-0-target-and-safety-workflow.md).

## Step 1: Inventory existing controls

- Purpose: Distinguish active checks, behavioral guidance, user documentation, and developer practice.
- Action: Record sources, triggers, ownership, and current coverage with evidence.
- Workflow: [Existing guardrail inventory](references/step-1-inventory-workflow.md).

## Step 2: Map the complete rule contract

- Purpose: Establish which of the bundled guardrail rules require equivalent
  enforcement on the target stack.
- Action: Resolve every bundled rule ID to implemented, conditional, or blocked with evidence.
- Workflow: [Rule contract mapping](references/step-2-rule-mapping-workflow.md).

## Step 3: Prepare concrete candidate checks

- Purpose: Produce runnable lint, type, bespoke, test, hook, and CI changes for the selected stack.
- Action: Draft candidates outside the target checkout and prepare positive and negative rule canaries.
- Workflow: [Candidate implementation](references/step-3-candidate-implementation-workflow.md).

## Step 4: Measure the dry-run impact

- Purpose: Compare whole-repository adoption with new/changed-code and selected-path scopes.
- Action: Count candidate file and line changes, baseline violations, rule gaps, and check costs.
- Workflow: [Dry-run impact](references/step-4-dry-run-workflow.md).

## Step 5: Approve merge and replacement choices

- Purpose: Let the user choose scope and treatment of every existing practice.
- Action: Present the action matrix and **STOP** before applying until decisions are explicit.
- Workflow: [Merge and replacement approval](references/step-5-approval-workflow.md).

## Step 6: Apply approved changes

- Purpose: Write only approved candidates while preserving unrelated repository work.
- Action: Apply in an isolated checkout, then wire one authoritative gate into local and CI paths.
- Workflow: [Approved application](references/step-6-apply-workflow.md).

## Step 7: Prove every applicable rule

- Purpose: Reject missing, uninvoked, or ineffective guardrails before completion.
- Action: Run the bundled coverage validator, rule canaries, focused tests, and the target gate.
- Workflow: [Rule coverage verification](references/step-7-verify-workflow.md).

## Output

### Result Format

```md
# RESULT

## Target and audience

- Repository, stack, CI provider, base ref, adoption scope, and protected paths.

## Existing controls

- Active versus optional/proposed instructions, checks, tests, and documentation by owner.

## Rule coverage

- Every bundled rule ID: applicable, implemented, not applicable with evidence, or blocked.

## Proposed actions

- For each action: keep/merge/replace/add/defer, target paths, rule IDs, and validation.

## Dry-run impact

- Whole-repo and scoped file counts, added/deleted lines, baseline violations, check costs, and unknowns.

## Decisions

- Explicit user choices or a statement that approval remains pending.

## Verification

- Commands and results, coverage-gate status, local/CI parity, and unresolved gaps.
```

Do not save a retained summary artifact. Keep candidate files temporary until the user approves application.
