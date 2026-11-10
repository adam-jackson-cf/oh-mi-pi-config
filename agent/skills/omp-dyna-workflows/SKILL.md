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

This skill applies only with dynaflows on. Use a standalone prompt line exactly
`$dynaflows`, `$dynaflows on` or `$dynaflows off` (case-insensitive); quoted mentions
and prose are not commands. Contradictory directive lines fail without writing.
The primary checkout's `.omp/dynaflows.json`, resolved through the Git common
directory, overrides `~/.omp/dynaflows.json`; linked worktrees inherit it. Neither
set means off. Outside a Git project, edit the machine default separately.

Choose exactly one active playbook:

- Evidence-only deliverable: [Investigation](references/playbooks/investigation.md).
- Missing controls, or user-visible work without a Verification surface in `.omp/test-profile.md`:
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
Read `skill://test-guidance/references/tiers.md`, `case-selection.md` and `suite-budget.md`
under that reference prefix, plus `.omp/test-profile.md`. Missing surfaces follow Start here.
Plan review is optional unless requested.

### 4. Implement

Main requires schema-v2 `.omp/gate.json` with `checks.commit`, `pathCheck` and an executable
pre-commit hook. `agent/extensions/agent-rails.ts` owns accepted schema fields/types;
`prove()` mirrors validation, and rails checks the hook at commit.
Missing controls or the lazy surface trigger above require the owner-approved project-setup PR.
Read `skill://setup-repository-guardrails` for proposal, Minimal/Full approval and application;
never apply before approval. Bootstrap installs the hook before its first commit.

Main follows `skill://git-tool/references/branch-and-integrate.md` to fetch the
resolved default and create the dedicated `agent/<slug>` branch/worktree, never
from spike code. Delegate edits, including small fixes, to sequential non-isolated tasks in that
worktree using absolute paths. Confinement is a brief invariant; rails reject enclosing-root
symlink escapes, not another worktree. Outside-repo scratch and internal URIs remain allowed.

1. Proof `task` writes proof files and stops uncommitted: no staging, push or PR.
2. Main calls `freeze()` on those bytes.
3. Separate implementation `task` makes the greening change and commits proofs
   with it, normally atomically. No proof may enter history red before its fix;
   split only if every earlier commit passes the hook with all proofs present.
   Main never commits. The writer pushes, writes the PR description, opens the PR
   and waits for exact-head green CI under git-tool's mechanics.

Each brief requires reads before editing. Proof briefs supply `.omp/test-profile.md` and
`skill://test-guidance/references/case-selection.md`, `naming-and-layout.md`, `outcome-tests.md`
and `tiers.md` under that reference prefix; add `property-tests.md`, `determinism.md`,
`behaviour-and-statistics.md` and `verification-surface.md` when applicable.
Proofs use outcome tests or lever journeys; journeys use defect with asserted expectRed or preserve,
never new-surface. Freeze required lever changes with runner-invoked journey files.
Implementation changes product code and feature-map docs, never frozen lever/proof bytes.
Implementation briefs supply `skill://coding-guidance/references/implementation-integrity.md`,
applicable `structure-and-grouping.md` and `naming.md` under that prefix,
`skill://test-guidance/references/before-commit.md`, and `verification-surface.md` when a mapped
feature changes (map docs only). Also supply `skill://git-tool`,
`skill://git-tool/references/branch-and-integrate.md` and `pr-description.md` under that prefix.
Both briefs include worktree, criteria, manifest, scope and task-specific references.
Require `skill://coding-guidance/references/verification.md` before writer handoff.
The writer lists gate paths/effects in **Gate changes** per
[patterns](references/patterns.md#gate-changes); listing alone is not a merge gate.

### 5. Verify

Main independently confirms exact-head green CI and no pending writer edits.
Follow `skill://git-tool/references/verify-loop.md` for risk tiers, durable fix
rounds, changed-head invalidation and evidence/label mechanics. Eligible docs-only
work uses a fresh `exempt_docs_only(..., target_ref=...)` plus green CI, without a
manifest or verifier. Runtime-loaded prose is not exempt.

For everything else main loads the template in Python eval and calls `prove()`
with objective and fetched PR `target_ref`. It assembles the brief and dispatches
the sole cross-family `verifier`; never spawn a second verifier or substitute a
`task`. Helper details live in [patterns](references/patterns.md#prove), and the
exact blocker text lives only in `templates/workflows.py`'s `BLOCKING_RULE`.
Main posts verification evidence and applies `agent-verified` only after this
risk-tier verification and exact-head CI pass. Rails enforce exact-head green
checks before the label always, and at merge only with dynaflows on. The checkout's
`.omp/gate.json` is the policy authority; `ciHost: nuc` requires a successful
`gate` commit status on that head, not a check run named `gate`. URL selectors
must match the checkout's primary GitHub repository. With dynaflows on, both gates
also require a valid pass marker; main applies the label only after both pass.
Every new SHA invalidates evidence.

### 6. Integrate

Main follows `skill://git-tool/references/merge-and-recover.md` for exact-head
eligibility, merge method, confirmation, serial integration and recovery. Git-tool
owns Git authority: `agentMerge: disabled` requires owner approval. Do not copy
merge or post-merge CI mechanics into playbooks.

## References and evals

- Refactors: `skill://coding-guidance/references/clean-cutovers.md`.
- Bugs: `skill://coding-guidance/references/regression-coverage.md`.
- Intermittent failures: `skill://test-guidance/references/flakes.md`.
- Owner correction: `skill://codify-omp-workflow-corrections`.
- `orchestrate`: frame, delegate independent units, prove each unit.
- `workflowz`: native `workpool()` for independent fan-out.
- `jevify`: deterministic prefilter, frozen rubric, calibrated `judge()` or
  `judge_batch()` for single-state properties; read `skill://jev-actions`.
  Contextual critique uses an independent cross-family `reviewer`, not Jev.
- Independent sessions do not share author context. Rails enforces verifier
  family independence against recorded editors/committers; see patterns.

## Templates

Load in a standalone Python eval cell:
`%load ~/.omp/agent/skills/omp-dyna-workflows/templates/workflows.py`.
Only `freeze`, `prove`, `exempt_docs_only` and `blind_label` are supported
orchestration APIs. `prove` and `blind_label` require eval's `agent()`/`wait()`
globals; `freeze` and `exempt_docs_only` also run in plain Python. Other functions
are internal helpers, even when plain-Python callable. Signatures, schemas, caps
and failure handling have one home: [Pattern contracts](references/patterns.md).
