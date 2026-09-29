---
name: "workflow-templates"
description:
  "USE WHEN running multi-agent work in OMP with the workflowz, jevify or orchestrate keywords, or
  when implementing with independent cross-family review, or producing blind labels for human
  confirmation."
---

# Workflow templates

A thin layer over OMP's built-in magic keywords. Use the keywords for the workflow itself; this
skill adds the owner's rules and two templates the keywords lack.

## Use the native keywords first

- `orchestrate`: plan the whole task, delegate parallel work to `task`, verify each phase.
- `workflowz`: eval `workpool()` fan-out, review lenses, judge panels, loop-until-dry,
  completeness critic. Independent items go in a pool; results arrive on their own; never block the
  session waiting.
- `jevify`: freeze the rubric before the data, filter deterministically, judge the bulk in one
  batch, read only what is flagged.

## Owner rules on top

- **Cross-family judging.** A judge, reviewer or second labeller never shares the author's model
  family. `require_cross_family(author, judge)` checks the live routes in `agent/config.yml`. In a
  `workflowz` judge panel, build the judge pool from an agent that passes this check (for example
  `task` designs judged by `plan-judge`).
- **Deterministic before Jev before LLM.** A real check command, pre-filter or rule runs first.
- **Caps that raise.** `MAX_AGENTS` (16) per wave; templates raise `WorkflowError` rather than
  silently dropping work.

## Templates

Load in a standalone Python eval cell:
`%load ~/.omp/agent/skills/workflow-templates/templates/workflows.py`.
Both are dependency-coupled, so they use `agent()` handles and `wait()` as the native contract
allows.

- `adversarial_verify(objective, repo, check_cmd, max_rounds=3)`: `task` implements; the repo's own
  check must pass before any review; `reviewer` (another family) tries to break it; its findings go
  back to `task`. Returns `pass` or `exhausted` with the full history.
- `blind_label(batch_files, rubric_path, labels)`: two blind labellers from different families per
  frozen batch. Returns `agreed` and `disputed`; the orchestrator settles disputes and a human
  confirms before any label is written.

Contracts and failure handling: `references/patterns.md`.
