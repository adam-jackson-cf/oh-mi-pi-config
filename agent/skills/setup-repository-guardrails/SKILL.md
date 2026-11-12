---
name: "setup-repository-guardrails"
description: "USE WHEN establishing or extending enforceable repository guardrails for a selected language and runtime stack."
hide: true
disable-model-invocation: true
---

# Workflow

The harness/repo boundary governs every step. The target repository never depends on, contains
or mentions omp or dynaflows. It keeps only conforming code and the controls the project would
want without the harness: build, tests, its own linters and checks, gate targets, hook config and
CI job. Harness opinions and their checks live in oh-mi-pi-config. Per-repo settings live in the
repo's **record**: `repos/<owner>/<name>/` under the oh-mi-pi-config root, resolved from the
clone's `origin` remote, gitignored and never committed. The
[example record](assets/example-record/README.md) defines its layout and rules.

## Step 0: Set target and safety boundary

- Purpose: Establish the Git checkout, its record path, chosen stack, setup mode, and protected work.
- Action: Check repository state, resolve `origin` to the record path, and record the setup
  mode: greenfield, adopt, or extend.
- Workflow: [Target and safety boundary](references/step-0-target-and-safety-workflow.md).

## Step 1: Inventory existing controls

- Purpose: Distinguish active checks, behavioral guidance, user documentation, and developer practice.
- Action: Record sources, triggers, ownership, make targets, hook config, CI job names and
  required checks with evidence.
- Workflow: [Existing guardrail inventory](references/step-1-inventory-workflow.md).

## Step 2: Map the complete rule contract

- Purpose: Establish which bundled guardrail rules the harness enforces and which the repo's own
  controls carry on the target stack.
- Action: Resolve every bundled rule ID to implemented, conditional, or blocked with evidence.
- Workflow: [Rule contract mapping](references/step-2-rule-mapping-workflow.md).

## Step 3: Draft the record and candidate fixes

- Purpose: Draft the record and the repo changes the harness checks need.
- Action: Run `omp-guardrails init --repo <clone> --record <draft record dir>` from the harness
  to draft the record outside the target checkout; author an adapter if the framework has none.
  Prepare candidate repo fixes in a temporary clone.
- Workflow: [Candidate implementation](references/step-3-candidate-implementation-workflow.md).
- Adapter: [Adapter authoring](references/adapter-authoring.md) when the test framework has none.

## Step 4: Measure the dry-run impact

- Purpose: Show every finding the harness checks report over the whole repository.
- Action: Count findings by rule and path, candidate file and line changes, and check costs.
- Workflow: [Dry-run impact](references/step-4-dry-run-workflow.md).

## Step 5: Approve the record and fixes

- Purpose: Let the owner confirm the record and the treatment of every existing practice.
- Action: Present the action matrix and **STOP** before applying until decisions are explicit.
- Workflow: [Record and fix approval](references/step-5-approval-workflow.md).

## Step 6: Apply approved changes

- Purpose: Write the confirmed record and only the approved repo fixes.
- Action: Write the record locally; land repo fixes in an isolated checkout through a normal PR.
- Workflow: [Approved application](references/step-6-apply-workflow.md).

## Step 7: Prove every applicable rule

- Purpose: Reject missing, uninvoked, or ineffective guardrails before completion.
- Action: Run the harness checks over the whole repo with the record, then the record's
  `checks.commit` and `checks.ci`.
- Workflow: [Rule verification](references/step-7-verify-workflow.md).

## Output

### Result Format

```md
# RESULT

## Target and audience

- Repository, record path, stack, CI runner role and required context (`ci.runner`,
  `ci.requiredContext`), base ref, scope exclusions, and protected paths.

## Existing controls

- Active versus optional/proposed instructions, checks, tests, and documentation by owner.

## Rule coverage

- Every bundled rule ID: applicable, implemented, not applicable with evidence, or blocked.

## Proposed actions

- For each action: keep/merge/replace/add/defer, target paths, rule IDs, and validation.

## Dry-run impact

- Whole-repo findings by rule and path, candidate file counts, added/deleted lines, check costs,
  and unknowns.

## Decisions

- Explicit user choices or a statement that approval remains pending.

## Verification

- Commands and results, harness-check status over the whole repo, local/CI parity, and
  unresolved gaps.
```

Do not save a retained summary artifact. Keep the draft record and candidate files temporary until the
owner approves application.
