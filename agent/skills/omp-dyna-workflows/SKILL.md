---
name: "omp-dyna-workflows"
description:
  "Use when handling non-trivial engineering requests or workflowz, jevify, orchestrate,
  independent-session review, evals, judgments, or blind labels for human confirmation."
---

# Task

## Start here

- **Purpose**: Route every non-trivial engineering request to one executable playbook.
- **When**: Features, bug fixes, refactors, investigations, project setup, or multi-phase work.
- Match the request to exactly one playbook below. Copy its numbered steps verbatim into the
  todo list as the first items, then execute them. The main session delegates every code change
  to `task`, including small fixes. Each `task` brief lists the reference URLs the current steps
  named and tells the subagent to read them before its first edit; the subagent never reads the
  whole skill.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Runbook: [Feature](references/playbooks/feature.md).
- Runbook: [Bug fix](references/playbooks/bug-fix.md).
- Runbook: [Refactor](references/playbooks/refactor.md).
- Runbook: [Investigation](references/playbooks/investigation.md).
- Runbook: [Project setup](references/playbooks/project-setup.md).
- Runbook: [Multi-phase](references/playbooks/multi-phase.md).

## Index

- **Purpose**: Load only the design guidance required by the current situation.
- **When**: A playbook reaches the corresponding action.
- Read hidden skills explicitly through these URLs; read only the referenced file, not its
  parent skill or unrelated references. A project's own test skill takes precedence.

| Situation | Reference |
| --- | --- |
| Before first edit | skill://coding-guidance/references/implementation-integrity.md |
| New file, folder, or move | skill://coding-guidance/references/structure-and-grouping.md |
| Naming or renaming | skill://coding-guidance/references/naming.md |
| Refactor, rename, replace | skill://coding-guidance/references/clean-cutovers.md |
| Bug fix | skill://coding-guidance/references/regression-coverage.md |
| Before handoff | skill://coding-guidance/references/verification.md |
| Before discovery: readiness | skill://codegraph/references/readiness-and-bootstrap.md |
| Before discovery: query choice | skill://codegraph/references/query-selection.md |
| Stale or changed graph | skill://codegraph/references/index-maintenance.md |
| Register graph MCP | skill://codegraph/references/mcp-registration.md |
| Graph unavailable or handoff | skill://codegraph/references/fallback-and-handoff.md |
| Select test tier | skill://test-guidance/references/tiers.md |
| Write unit tests | skill://test-guidance/references/unit-tests.md |
| Control nondeterminism | skill://test-guidance/references/determinism.md |
| Behaviour or measured claims | skill://test-guidance/references/behaviour-and-statistics.md |
| Intermittent failure | skill://test-guidance/references/flakes.md |
| Before committing | skill://test-guidance/references/before-commit.md |
| Test-suite growth | skill://test-guidance/references/suite-budget.md |
| Project setup or new guardrails | skill://setup-repository-guardrails |
| Any Git action: authority, then branch | skill://git-tool |
| Independent review loop | skill://git-tool/references/review-loop.md |
| Merge or recover red main | skill://git-tool/references/merge-and-recover.md |
| Owner corrects repeated mistake | skill://correct |
| Evals | skill://jev-actions |

## Use the native keywords first

- `orchestrate`: plan the whole task, delegate parallel work to `task`, verify each phase.
- `workflowz`: eval `workpool()` fan-out, review lenses, judge panels, loop-until-dry,
  completeness critic. Independent items go in a pool; results arrive on their own; never block the
  session waiting.
- `jevify`: freeze the rubric before the data, filter deterministically, judge the bulk in one
  batch, read only what is flagged.

## Owner rules on top

- **Independent-session judging.** A judge, reviewer or second labeller runs in its own session
  with none of the author's context (every `agent()` spawn qualifies). Never let the author's own
  session judge its work.
- **Cross-family judges.** A judge comes from a different model family than the author of the work
  it judges. Sol 6.1 implements (`task`), so `reviewer`, `test-judge` and `completionist` run on
  Sonnet 5.5; Opus 5.5 plans, so `plan-judge` and `kiss` run on Sol 6.1 (`agent/config.yml`). The
  Opus main session delegates every code change to `task`, including small fixes, so review stays
  cross-family. `innovation-council` is acceptable as a reviewer: it mixes families through its
  challenger and peer.
- **Evals: deterministic, then Jev, then a cross-family judge.**
  1. A real check command, pre-filter or rule runs first.
  2. Use Jev only for a property observable in the single state itself, such as "contains an
     unlabelled timing" or "names a test file": eval `judge()` for one state, `judge_batch()` for
     many. Read `skill://jev-actions` first, put a cheap deterministic prefilter in front, and
     check the threshold on a small labelled sample before relying on it. `judge()` falls back
     through the `judge` role chain on its own.
  3. Intent, appropriateness, phase or relevance questions, anything that needs context beyond
     the state, and open-ended critique or multi-defect review go to a cross-family judge agent
     from the list above.
- **Caps that raise.** `MAX_AGENTS` (16) per wave; templates raise `WorkflowError` rather than
  silently dropping work.

## Templates

Load in a standalone Python eval cell:
`%load ~/.omp/agent/skills/omp-dyna-workflows/templates/workflows.py`.
Both are dependency-coupled, so they use `agent()` handles and `wait()` as the native contract
allows.

- `adversarial_verify(objective, repo, check_cmd, max_rounds=3)`: `task` implements; the repo's own
  check must pass before any review; `reviewer` (independent session) tries to break it; its
  findings go back to `task`. Returns `pass` or `exhausted` with the full history.
- `blind_label(batch_files, rubric_path, labels)`: two blind labellers (distinct agents, separate
  sessions) per frozen batch. Returns `agreed` and `disputed`; the orchestrator settles disputes
  and a human confirms before any label is written.

Contracts and failure handling: `references/patterns.md`.
