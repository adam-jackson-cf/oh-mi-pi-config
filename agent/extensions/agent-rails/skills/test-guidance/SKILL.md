---
name: "test-guidance"
description: "Use when setting up how a repository writes and maintains its tests, from an analysis of its existing test suite."
hide: true
disable-model-invocation: true
---

# Workflow

Supporting references: [suite-signals](references/suite-signals.md), [placement](references/placement.md), [tiers](references/tiers.md)

### Step 0: Map the repository's testing homes

- **Purpose**: Establish the Git target and record where it already keeps testing rules, commands, helpers and agent procedures.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Change no repository content before Step 5.
- Workflow: [references/step-0-map-workflow.md](references/step-0-map-workflow.md)

### Step 1: Compare rules statically

- **Purpose**: Record which approach rules the repository states, contradicts or lacks, and how its tests practise them.
- Read files and search only; run nothing.
- Workflow: [references/step-1-compare-workflow.md](references/step-1-compare-workflow.md)

### Step 2: Choose adoption

- **Purpose**: Let the owner choose how much of the approach to adopt, from facts, before any test run.
- **STOP**: Gates every test run and plan on the owner's option, migration breadth and conflict decisions.
- Workflow: [references/step-2-adoption-workflow.md](references/step-2-adoption-workflow.md)

### Step 3: Run the baseline in place

- **Purpose**: Record each tier's selection, and the full suite results when the plan can change code.
- Workflow: [references/step-3-baseline-workflow.md](references/step-3-baseline-workflow.md)

### Step 4: Sign the implementation plan

- **Purpose**: Show every object the run will write, where and why, as one plan.
- **STOP**: Gates every repository change on the owner's approval of the plan.
- Workflow: [references/step-4-plan-workflow.md](references/step-4-plan-workflow.md)

### Step 5: Apply and verify

- **Purpose**: Write the approved rows in place and show the suite, the stated commands and every route still work.
- Never stage, commit or push.
- Workflow: [references/step-5-apply-workflow.md](references/step-5-apply-workflow.md)

## Output

### Result Format

- `Target and choice`: repository, stack, adoption option and migration breadth.
- `Rule map`: per rule group, the stated, contradicted and missing rules and the practice verdict, with one line of evidence.
- `Applied`: each applied row with its path and action, including pointers and the symlink.
- `Not adopted`: declined rules, conflicts kept in the repository's favour and rejected rows, and a note that this skill
  installed no hook, CI job or checker.
- `Verification`: results beside the baseline, route checks, and the target's `git status` and `git diff --stat`.
- `Follow-up`: under breadth `none`, the ranked migration candidates as tasks for the installed skill; at any breadth,
  each mechanism gap as a task for the repository's owner; else `none`.
