---
name: "dynaflows"
description:
  "Use when the owner asks for non-trivial engineering work, orchestration, proof-first verification,
  judgments or blind labels in a repo with dynaflows on."
hide: true
disable-model-invocation: true
---

# Workflow

Supporting references: [delivery principles](references/delivery-principles.md),
[structure and naming](references/structure-and-naming.md), [test enforcement](references/test-enforcement.md),
[proofs](references/proofs.md), [task briefs](references/task-briefs.md), [verify loop](references/verify-loop.md),
[patterns](references/patterns.md), [freeze](references/freeze.md), [prove](references/prove.md),
[structure review](references/structure-review.md), [blind label](references/blind-label.md),
[dependency advisories](references/dependency-advisories.md), [harness setup](references/harness-setup.md),
[setup inventory](references/setup-inventory.md), [setup rule mapping](references/setup-rule-mapping.md),
[setup candidates](references/setup-candidates.md), [setup approval](references/setup-approval.md),
[setup cutover](references/setup-cutover.md), [adapter authoring](references/adapter-authoring.md),
playbooks [investigation](references/investigation.md), [bug fix](references/bug-fix.md),
[refactor](references/refactor.md), [feature](references/feature.md), [multi-phase](references/multi-phase.md),
[project setup](references/project-setup.md)

## Step 0: Preflight the switch, record and playbook

- **Purpose**: Confirm dynaflows is on, resolve the repo's record and select exactly one playbook.
- **When**: Run on every request before Frame, and on every owner on/off ask.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- A missing or refused record starts or resumes [harness setup](references/harness-setup.md).
- Workflow: [references/step-0-preflight-workflow.md](references/step-0-preflight-workflow.md)

## Step 1: Frame

- **Purpose**: Define the requested outcome, every acceptance criterion and only plan-changing questions.
- Workflow: [references/step-1-frame-workflow.md](references/step-1-frame-workflow.md)

## Step 2: Spike

- **Purpose**: Answer remaining questions in bounded scratch experiments and discard all spike code.
- **When**: Bug reproduction and project-setup inventory always spike; Investigation ends here.
- Workflow: [references/step-2-spike-workflow.md](references/step-2-spike-workflow.md)

## Step 3: Plan

- **Purpose**: Map every criterion to a named proof that fails at base and passes at head.
- Workflow: [references/step-3-plan-workflow.md](references/step-3-plan-workflow.md)

## Step 4: Implement

- **Purpose**: Freeze proofs, then land the greening change through tasks in a dedicated worktree.
- **When**: Only with a valid schema-v2 record; missing controls route to Project setup first.
- **STOP**: Gates implementation when the commit gate reports `SECURITY.dependency-audit` findings.
- Main never commits, patches implementation or writes the PR body.
- Workflow: [references/step-4-implement-workflow.md](references/step-4-implement-workflow.md)

## Step 5: Verify

- **Purpose**: Run `prove()` then `structure_review()` on the PR's exact current head.
- Workflow: [references/step-5-verify-workflow.md](references/step-5-verify-workflow.md)

## Step 6: Integrate

- **Purpose**: Merge the verified exact head under git-tool, or recover a red default branch by revert.
- **STOP**: Gates merge on exact-head CI green, prove `status: pass` and structure `halt: false`.
- Workflow: [references/step-6-integrate-workflow.md](references/step-6-integrate-workflow.md)

## Output

### Result Format

- Stage reached, active playbook and record state.
- PR and exact head, CI status, prove status and structure `halt`.
- Blockers and owner decisions still pending.
