---
name: "coding-guidance"
description: "Use when a task creates, changes, moves or restructures code or project folders in any language or runtime."
hide: true
disable-model-invocation: true
---

# Task

## Implementation integrity

- **Purpose**: Prevent placeholders, fake fallbacks, and quality-gate bypasses.
- **When**: Every coding task, before the first edit.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Runbook: [Implementation integrity](references/implementation-integrity.md)

## Structure and grouping

- **Purpose**: Place every file where its owner and dependency direction can be read from its path.
- **When**: Creating a file, folder, module or package, moving code, or adding a dependency between parts.
- Runbook: [Structure and grouping](references/structure-and-grouping.md)

## Naming

- **Purpose**: Name things for what they hold or do, with one name per concept.
- **When**: Naming or renaming a folder, file, module, command, flag, record field or concept.
- Runbook: [Naming](references/naming.md)

## Clean cutovers

- **Purpose**: Make the target design canonical without legacy compatibility.
- **When**: Refactoring, renaming, moving or replacing behaviour, including a restructure.
- Runbook: [Clean cutovers](references/clean-cutovers.md)

## Regression coverage

- **Purpose**: Identify and flag the failing behaviour, instead of pinning the one scenario that exposed it.
- **When**: Fixing a bug.
- Runbook: [Regression coverage](references/regression-coverage.md)

## Verification

- **Purpose**: Scale verification to risk and blast radius.
- **When**: Before handing off any coding task.
- Runbook: [Verification](references/verification.md)
