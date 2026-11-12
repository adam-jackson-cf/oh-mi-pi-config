---
name: "omp-dyna-workflows"
description:
  "Use when handling non-trivial engineering requests or workflowz, jevify, orchestrate,
  proof-first verification, evals, judgments, or blind labels for human confirmation."
hide: true
disable-model-invocation: true
---

# Task

## Start here

This skill applies only with dynaflows on. Each repo's settings live in its **record**:
`repos/<owner>/<name>/` under the oh-mi-pi-config root, named by the clone's `origin`
remote (lowercase; worktrees resolve like their main checkout), gitignored and never
committed. It holds `gate.json` and `profile.md`; see
`skill://setup-repository-guardrails/assets/example-record/README.md`. The repo itself
holds nothing about omp or dynaflows. The record's `dynaflows.enabled` is the switch;
no `origin` remote or no record means off, and a machine default `~/.omp/dynaflows.json` of
`{"enabled": false}` turns it off for every repo on that machine. Use a standalone prompt line exactly
`$dynaflows`, `$dynaflows on` or `$dynaflows off` (case-insensitive) to set it; quoted
mentions and prose are not commands. Contradictory directive lines fail without writing.

Choose exactly one active playbook:

- Evidence-only deliverable: [Investigation](references/playbooks/investigation.md).
- Missing controls, or user-visible work without a Verification surface in the record's `profile.md`:
  [Project setup](references/playbooks/project-setup.md) is a prerequisite PR.
  An owner-approved no-runnable-app surface exemption does not trigger setup.
- Multiple PRs or flagged delivery: [Multi-phase](references/playbooks/multi-phase.md).
- Otherwise the primary deliverable selects [Bug fix](references/playbooks/bug-fix.md),
  behaviour-preserving [Refactor](references/playbooks/refactor.md), or
  [Feature](references/playbooks/feature.md).

An investigation report seeds Frame of a separately requested implementation; it
is not permission to implement. Playbooks contain deltas to the contract below.

## Six-stage contract

### 1. Frame

Main defines the requested outcome, every acceptance criterion and only questions
whose answers change the plan. `kiss` cuts unnecessary work and questions, never
requested behaviour. Discover owners and callers in this order: `find` for
behaviour and concepts (`-k` synonyms; repeat until no new files appear), `lsp`
for definitions, references and implementations, `ast_grep` for structure, then
`grep`/`glob` for exact text.

### 2. Spike

Use the [spike recipe](references/patterns.md#spike-recipe) for remaining questions.
Bug reproduction and project-setup inventory are mandatory spike activities.
Main declares predicates and side effects, delegates bounded scratch work, saves
the findings before removing the tree, and discards all spike code. Investigation
ends here with its report; other playbooks continue in order.

### 3. Plan

Keep the plan draft until spike answers land. Map every criterion to a named proof
in a draft manifest using [proof contracts](references/patterns.md#proof-manifest).
Every proof follows one rule: it fails at base and passes at head (TDD).
A PR with no behaviour change (refactor, docs, project setup without new controls) maps no
proofs and freezes an empty manifest; its PR check is CI green only.
Read `skill://test-guidance/references/tiers.md`, `case-selection.md` and `suite-budget.md`
under that reference prefix, plus the record's `profile.md`. Missing surfaces follow Start here.
Plan review is optional unless requested.

### 4. Implement

Main requires a record whose schema-v2 `gate.json` has `pathCheck`; `checks.commit` is optional.
`agent/extensions/agent-rails.ts` owns accepted schema fields/types; `prove()` mirrors
validation. At each agent commit, rails run the record's `pathCheck`, its `checks.commit` when
set (the repo's own command) and then the harness checks (`omp-guardrails`, run from the harness
with the record as config) over the whole repo; any finding blocks the commit, with no baseline
or diff scope. Missing controls or the lazy surface trigger above require owner-approved project
setup. Read `skill://setup-repository-guardrails` for the record draft, Minimal/Full approval and
application; never apply before approval.

Main follows `skill://git-tool/references/branch-and-integrate.md` to fetch the
resolved default and create the dedicated `agent/<slug>` branch/worktree, never
from spike code. Delegate edits, including small fixes, to sequential non-isolated tasks in that
worktree using absolute paths. Confinement is a brief invariant; rails reject enclosing-root
symlink escapes, not another worktree. Outside-repo scratch and internal URIs remain allowed.

1. Proof `task` writes proof files and stops uncommitted: no staging, push or PR.
2. Main calls `freeze()` on those bytes; freeze shows every proof red at base.
   With no proofs, main freezes an empty manifest and skips step 1.
3. Separate implementation `task` makes the greening change and commits proofs
   with it, normally atomically. It never edits frozen proof bytes.
   Main never commits. The writer pushes, writes the PR description, opens the PR
   and waits for exact-head green CI under git-tool's mechanics.

Each brief requires reads before editing. Proof briefs supply the record's `profile.md` path and
`skill://test-guidance/references/case-selection.md`, `naming-and-layout.md`, `outcome-tests.md`
and `tiers.md` under that reference prefix; add `property-tests.md`, `determinism.md`,
`behaviour-and-statistics.md` and `verification-surface.md` when applicable.
Proofs use outcome tests or lever journeys. Freeze required lever changes with runner-invoked
journey files. Implementation changes product code and feature-map docs, never frozen
lever/proof bytes.
Implementation briefs supply `skill://coding-guidance/references/implementation-integrity.md`,
applicable `structure-and-grouping.md` and `naming.md` under that prefix,
`skill://test-guidance/references/before-commit.md`, and `verification-surface.md` when a mapped
feature changes (map docs only). Also supply `skill://git-tool`,
`skill://git-tool/references/branch-and-integrate.md` and `pr-description.md` under that prefix.
Both briefs include worktree, criteria, manifest, scope and task-specific references.
Require `skill://coding-guidance/references/verification.md` before writer handoff.

### 5. Verify

Main confirms the writer has no pending edits, then calls `prove()` against the PR's current
head ([patterns](references/patterns.md#prove)). `prove()` checks two things:

1. The record's declared required CI context is green on the exact head.
2. The frozen proof bytes are unchanged and every proof passes at head (twice; journeys 3 times).

With an empty manifest it checks CI green only. It writes its result to
`<git-common-dir>/omp-prove/<head>.result.json` and posts it on the PR. That file with
`status: pass` for the exact head is the one pass signal: with dynaflows on, rails' `gh pr merge`
guard requires it and CI green on that head. A rebase or other branch update needs nothing extra:
run `prove()` on the new head before merge. No re-freeze, review agent or status posting.
Mechanics live in `skill://git-tool/references/verify-loop.md`.

### 6. Integrate

Main follows `skill://git-tool/references/merge-and-recover.md` for merge method,
confirmation and recovery. Git-tool owns Git authority: `agentMerge: disabled` requires owner
approval. Do not copy merge mechanics into playbooks.

## References and evals

- Refactors: `skill://coding-guidance/references/clean-cutovers.md`.
- Bugs: `skill://coding-guidance/references/regression-coverage.md`.
- Intermittent failures: `skill://test-guidance/references/flakes.md`.
- Owner correction: `skill://codify-omp-workflow-corrections`.
- `orchestrate`: frame, delegate independent units, prove each unit.
- `workflowz`: native `workpool()` for independent fan-out.
- `jevify`: deterministic prefilter, frozen rubric, calibrated `judge()` or
  `judge_batch()` for single-state properties; read `skill://jev-actions`.

## Templates

Load in a standalone Python eval cell:
`%load ~/.omp/agent/skills/omp-dyna-workflows/templates/workflows.py`.
Only `freeze`, `prove` and `blind_label` are supported orchestration APIs.
`blind_label` requires eval's `agent()`/`wait()` globals; `freeze` and `prove` also run in
plain Python. Other functions are internal helpers, even when plain-Python callable.
Signatures, schemas, caps and failure handling have one home:
[Pattern contracts](references/patterns.md).
