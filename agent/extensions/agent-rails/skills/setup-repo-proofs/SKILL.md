---
name: "setup-repo-proofs"
description: "Use when establishing or extending enforceable verification controls in a Git repository for its language and runtime stack."
hide: true
disable-model-invocation: true
---

# Workflow

Supporting references: [design-principles](references/design-principles.md), [verification-layers](references/verification-layers.md),
[tooling-families](references/tooling-families.md), [structure-and-naming](references/structure-and-naming.md),
[test-evidence](references/test-evidence.md), [dependency-audit](references/dependency-audit.md), [gate-wiring](references/gate-wiring.md),
[run-state](references/run-state.md)

### Step 0: Set target and safety boundary

- **Purpose**: Establish the Git checkout, stack, rollout scope (whole, changed or selected paths), setup modes and protected work.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Workflow: [references/step-0-target-and-safety-workflow.md](references/step-0-target-and-safety-workflow.md)

### Step 1: Inventory existing controls

- **Purpose**: Separate deterministically enforced checks from instructions and documentation, with rule-ID, lock-file and manifest evidence.
- Workflow: [references/step-1-inventory-workflow.md](references/step-1-inventory-workflow.md)

### Step 2: Map the complete rule contract

- **Purpose**: Resolve every catalogue rule to an implementation, an evidenced conditional exclusion or a blocker, with one owner per family.
- Workflow: [references/step-2-rule-mapping-workflow.md](references/step-2-rule-mapping-workflow.md)

### Step 3: Prepare concrete candidate checks

- **Purpose**: Produce runnable tool configs, checkers, test evidence, hook, CI and `proofs.toml` candidates outside the target.
- Workflow: [references/step-3-candidate-implementation-workflow.md](references/step-3-candidate-implementation-workflow.md)

### Step 4: Measure the dry-run impact

- **Purpose**: Compare whole-repository adoption with new/changed-code and selected-path scopes before any target edit.
- Workflow: [references/step-4-dry-run-workflow.md](references/step-4-dry-run-workflow.md)

### Step 5: Approve scope, tier and replacements

- **Purpose**: Make the owner's explicit decisions the boundary before any target write.
- **STOP**: Gates every target write on the owner's choice of scope and tier and approval of exact paths and merge/replace decisions.
- Workflow: [references/step-5-approval-workflow.md](references/step-5-approval-workflow.md)

### Step 6: Apply approved changes

- **Purpose**: Land only approved changes in an isolated checkout, leaving one authority per rule and unrelated work intact.
- Workflow: [references/step-6-apply-workflow.md](references/step-6-apply-workflow.md)

### Step 7: Prove every applicable rule

- **Purpose**: Reject missing, unenforced or ineffective controls with the validator, canaries and the real enforcement points.
- Workflow: [references/step-7-verify-workflow.md](references/step-7-verify-workflow.md)

## Output

### Result Format

Return these sections in the terminal:

```md
# RESULT

## Target and audience
- Repository, stack, CI provider, base ref, rollout scope, setup modes, exclusions and protected paths.

## Existing controls
- Active versus manual, proposed, stale or unverified instructions, checks, tests and documentation by owner and trigger.

## Rule coverage
- Every catalogue ID: implemented (by which control), not applicable with evidence and approval, or blocked.
- Every lock file and manifest: the audit that covers it, or the fix.

## Proposed actions
- For each action: keep/merge/replace/add/defer, target paths, rule IDs and verification.

## Dry-run impact
- Whole-repository and scoped findings by rule and path, file and line counts, measured costs, estimates and unknowns.

## Decisions
- Explicit owner choices, or a statement that approval remains pending.

## Verification
- Commands and results, validator status, enforcement points and their parity, host protection, kept run state and unresolved gaps.
```

Keep run state in the excluded `.proofs/` folder and candidate files in the temporary candidate root until the owner approves
application; no check reads either.
