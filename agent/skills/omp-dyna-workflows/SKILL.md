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
- Missing approved implementation controls: [Project setup](references/playbooks/project-setup.md)
  is a prerequisite PR; complete it before resuming the original request.
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
requested behaviour. Before discovery read
`skill://codegraph/references/readiness-and-bootstrap.md`, then
`skill://codegraph/references/query-selection.md`; discover owners and callers.

### 2. Spike

Use the [spike recipe](references/patterns.md#spike-recipe) for remaining questions.
Bug reproduction and project-setup inventory are mandatory spike activities.
Main declares predicates and side effects, delegates bounded scratch work, saves
the findings before removing the tree, and discards all spike code. Investigation
ends here with its report; other playbooks continue in order.

### 3. Plan

Keep the plan draft until spike answers land. Map every criterion to a named proof
in a draft manifest using [proof contracts](references/patterns.md#proof-manifest).
Missing proof surfaces are first tasks. Read `skill://test-guidance/references/tiers.md`
and `skill://test-guidance/references/suite-budget.md` when selecting coverage.
Plan review is optional unless the owner requests approval.

### 4. Implement

Main checks prerequisites before ordinary implementation: a valid schemaVersion 2
`.omp/gate.json` with `checks.commit`, `pathCheck`, and an installed executable
pre-commit hook. Schema fields are `schemaVersion`, `checks` (`commit`, `ci`),
`pathCheck`, `pathCheckTimeoutMs`, `testGlobs`, `agentMerge` and `ciHost`; accepted
types/values are owned by `agent/extensions/agent-rails.ts`. `prove()` mirrors
schema validation, while rails checks the installed hook at `git commit`.
Missing controls require owner approval
and the project-setup prerequisite PR. Read `skill://setup-repository-guardrails`
for its proposal, Minimal/Full approval and application protocol; do not apply
controls before approval. Bootstrap installs the approved hook before its first
commit, not before proposing the controls.

Main follows `skill://git-tool/references/branch-and-integrate.md` to fetch the
resolved default and create the dedicated `agent/<slug>` branch/worktree, never
from spike code. Delegate code edits, including small fixes. Tasks run non-isolated,
sequentially in that worktree with absolute paths. Assigned-worktree confinement
is a brief/instruction invariant: the edit rail requires absolute filesystem paths
and rejects symlink escapes from enclosing Git roots, not selection of a particular
assigned worktree. Absolute outside-repository scratch paths remain allowed;
internal URI paths are exempt from this filesystem-path check.

1. Proof `task` writes proof files and stops uncommitted: no staging, push or PR.
2. Main calls `freeze()` on those bytes.
3. Separate implementation `task` makes the greening change and commits proofs
   with it, normally atomically. No proof may enter history red before its fix;
   split only if every earlier commit passes the hook with all proofs present.
   Main never commits. The writer pushes, writes the PR description, opens the PR
   and waits for exact-head green CI under git-tool's mechanics.

Each task brief requires explicit reads before editing. The proof brief supplies
`skill://test-guidance/references/unit-tests.md`,
`skill://test-guidance/references/determinism.md`,
`skill://test-guidance/references/behaviour-and-statistics.md` and
`skill://test-guidance/references/tiers.md`.
The implementation brief supplies
`skill://coding-guidance/references/implementation-integrity.md`, applicable
`skill://coding-guidance/references/structure-and-grouping.md` and
`skill://coding-guidance/references/naming.md`,
`skill://test-guidance/references/before-commit.md`, `skill://git-tool`,
`skill://git-tool/references/branch-and-integrate.md` and
`skill://git-tool/references/pr-description.md`. Both briefs include the worktree,
criteria, manifest, allowed scope and applicable task-specific references.
Require `skill://coding-guidance/references/verification.md` before writer handoff.
The writer lists discovered gate paths/effects in optional **Gate changes** per
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
- Graph maintenance/access: `skill://codegraph/references/index-maintenance.md`,
  `mcp-registration.md`, `fallback-and-handoff.md` under the same reference prefix.
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
