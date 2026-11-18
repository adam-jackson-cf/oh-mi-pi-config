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
`repos/<owner>/<name>/` under the oh-mi-pi-config root, named by the clone's `origin` remote
(lowercase; worktrees resolve like their main checkout), gitignored and never committed. It holds
`gate.json` and `profile.md`; see
`agent/lib/record/README.md`. The repo itself holds nothing
about omp or dynaflows. The record's `dynaflows.enabled` is the switch; no `origin` remote or no
record means off, and a machine default `~/.omp/dynaflows.json` of `{"enabled": false}` turns it off
for every repo on that machine. The owner turns it on or off in the main chat. A standalone
directive line (case-insensitive) sets it at once: `$dynaflows`, `$dynaflows` followed by an on verb
(`on`, `enable`, `init`, `initialise`, `start`, `set up`, `turn on`) or an on verb before
`$dynaflows`; `$dynaflows off|disable|stop` or `disable|stop|turn off $dynaflows` for off. Two
contradictory lines change nothing and the agent asks which. Any other wording, such as "I want to
enable dynaflows", is yours to judge: when the owner is asking (not asking about it, quoting it or
negating it), call the `dynaflows` tool; never call it on your own initiative. Agents write records
directly, with approval fields only as the owner stated them in the chat. Turning it on
with no record starts `skill://omp-dyna-workflows/references/harness-setup.md`, and the agent
writes the record,
`dynaflows.enabled` true, itself; a refused record resumes setup and the agent repairs it; a prompt
that is only the ask asks for this selection: report the stage found and start it, or, with setup
complete, ask the owner for the task.

Choose exactly one active playbook:

- Evidence-only deliverable: [Investigation](references/playbooks/investigation.md).
- Missing controls, user-visible work without a Verification surface and feature map
  (`verification_surface.feature_map_path`) in the record's `profile.md`, or a harness check step
  whose rules the repo's checkers hold, or could hold, that the record has not listed in
  `guardrails.check.repo` (except harness-owned per-edit `path-check`, which always uses the
  record's required `guardrails.structure`, `guardrails.naming` and `guardrails.paths` settings):
  [Project setup](references/playbooks/project-setup.md) is a prerequisite PR.
  An owner-approved no-runnable-app surface exemption does not trigger setup; it declares no
  feature map, so `freeze()` accepts no proofs there.
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
Proofs are outcome tests (or lever journeys) tied to the feature map: each criterion names the
feature-map entry, under the record profile's `verification_surface.feature_map_path`, that will
link its proof file. A repo whose record declares no feature map gets one through Project setup
before any proof is frozen.
A PR with no behaviour change (refactor, docs, project setup without new controls) maps no
proofs, changes no feature-map doc and freezes an empty manifest;
its PR check still checks CI and rename residue.
Read `skill://test-guidance/references/tiers.md`, `case-selection.md` and `suite-budget.md`
under that reference prefix, plus the record's `profile.md`. Missing surfaces follow Start here.
Plan review is optional unless requested.

### 4. Implement

Read [proofs](references/proofs.md) for proof/freezing and record-binding requirements.

Before ordinary implementation, main requires a valid pathCheck-free schema-v2 record:
`gate.json` has `checks.commit`, and `profile.md` supplies complete path-check settings.
No record or missing controls requires the owner-approved Project setup prerequisite; bootstrap may
propose the record and repository fixes before controls exist, never bypass approval.
A missing required field or a field outside the schema (including obsolete `checks.pathCheck`)
fails to load with an error naming the field. `agent/lib/repo-record.ts` (`gateSchema`) owns
accepted schema fields/types; its Python twin `agent/lib/repo_record.py` (`gate_reason`) serves
`prove()` and CI. Rails run harness `omp-guardrails path-check --repo <worktree> --record <record
dir>`
after edits and before commit, configured by required profile `guardrails.structure`,
`guardrails.naming` and `guardrails.paths`. At each agent commit, this is followed by the record's
`checks.commit` (the repo's own command) and whole-repo harness checks (`omp-guardrails check`),
minus the steps `guardrails.check.repo` gives to repo checkers that `checks.commit` already runs.
Any finding blocks the commit, with no baseline or diff scope for whole-repo checks.
Per-edit path checks are the principles 1–2 exception in [ADR
0002](../../../../../docs/adr/0002-harness-owns-path-checks.md).
When the commit gate reports `SECURITY.dependency-audit` findings, main stops implementation and
follows [dependency advisories](references/patterns.md#dependency-advisories): each advisory gets
the calibrated severity judgment, except a malicious package (`MAL-`), which code routes to the
owner as critical. Any high or critical advisory, advisory with no fixed release, or fix blocked
by a pinned dependent halts the workflow with a report to the owner. Low or medium advisories
with unblocked fixes are upgraded in the flow; blocked fixes need a dated owner exception.
Agents never edit the record's `ownerOnlyPaths`, such as the exception file; rails refuse the
commit.
Missing controls or the lazy surface trigger above require owner-approved project setup. Read
`skill://omp-dyna-workflows/references/harness-setup.md` for the record draft, Minimal/Full
approval and application;
never apply before approval.

Main follows `skill://git-tool/references/branch-and-integrate.md` to fetch the
resolved default and create the dedicated `agent/<slug>` branch/worktree, never
from spike code. Delegate edits, including small fixes, to sequential non-isolated tasks in that
worktree using absolute paths. Rails reject enclosing-root symlink escapes and refuse
`git worktree add` outside `<worktree.base>/<repo>/<slug>`; the assigned worktree for edit-tool
writes stays an instruction boundary. Outside-repo scratch and internal URIs remain allowed.

Proof precedes implementation; main never commits, patches implementation or writes the PR body.
Use sequential non-isolated tasks, not isolated patch-return modes:

1. Proof `task` writes proof files and the feature-map entries that link them, and stops
   uncommitted: no staging, push or PR.
2. Main calls `freeze()` on those bytes; freeze refuses a proof file no feature-map doc links
   and shows every proof red at base. Map docs are not frozen and may stay dirty.
   With no proofs, main freezes an empty manifest and skips step 1.
3. Separate implementation `task` makes the greening change and commits proofs and map docs
   with it, normally atomically. It never edits frozen proof bytes.
4. **Discoveries.** When implementation finds user-facing behaviour the plan did not name, the
   implementation task adds or updates its feature-map entry and writes its outcome test, leaves
   both uncommitted with no other pending edits, and stops. Main re-freezes with
   `previous=(path, digest)` (and `revise` only for changed existing proofs); the new test is red
   at base because the behaviour did not exist there. Implementation then resumes. There is no
   "added after freeze" exception.
5. **Map sweep.** Before the PR opens, a sweep `task` lists every user-facing behaviour the
   branch diff adds or changes, each with a concrete source path; an item without one is dropped.
   Each item not covered by a feature-map doc changed in the diff gets a map entry and an outcome
   test, handled as a discovery (re-freeze). The sweep writes map entries and tests only: no
   review findings, finding schema or reviewer step.
6. Main never commits. The writer pushes, writes the PR description, opens the PR
   and waits for exact-head green CI under git-tool's mechanics.

The writer owns the PR body and later corrections, using existing reported evidence only.
Full proof evidence belongs in `prove()`'s separate result comment, outside the PR body's word
budget.

Each brief requires reads before editing. Proof briefs supply the record's `profile.md` path and
`skill://test-guidance/references/case-selection.md`, `naming-and-layout.md`, `outcome-tests.md`
and `tiers.md` under that reference prefix; add `property-tests.md`, `determinism.md`,
`behaviour-and-statistics.md` and `verification-surface.md` when applicable.
Proofs use outcome tests or lever journeys. Freeze required lever changes with runner-invoked
journey files. Implementation changes product code and feature-map docs, never frozen
lever/proof bytes; a new proof comes only through a discovery re-freeze.
Implementation briefs supply `skill://omp-dyna-workflows/references/delivery-principles.md`,
applicable `structure-and-naming.md` and `proofs.md` under that prefix,
`skill://test-guidance/references/before-commit.md`, `verification-surface.md` and
`outcome-tests.md` (map entries and discovery tests). Also supply `skill://git-tool`,
`skill://git-tool/references/branch-and-integrate.md` and `pr-description.md` under that prefix.
Both briefs include worktree, criteria, manifest, scope and task-specific references.
Sweep briefs supply the base and head, the record's feature-map path and
`skill://test-guidance/references/verification-surface.md` and `outcome-tests.md` under that prefix.
Rails require `skill://test-guidance/references/before-commit.md` before any writer handoff.

### 5. Verify

The valid pathCheck-free schema-v2 record from stage 4 remains required for verification.
Main confirms the writer has no pending edits, then calls `prove()` against the PR's current
head ([patterns](references/patterns.md#prove)). `prove()` checks four things:

1. The record's declared required CI context is green on the exact head.
2. Harness `omp-guardrails rename-residue --base <parent> --head <head>` finds no retired
   names left in renamed folders or files, after CI is green and before proofs. Every finding
   blocks, including with an empty manifest. This diff-scoped structure pre-check is the
   principles 5/7 exception in [ADR
0003](../../../../../docs/adr/0003-structure-checks-at-pr-diff-scope.md).
3. Every feature-map doc changed in `parent..head` links at least one proof file in the manifest;
   otherwise it blocks with those doc paths (add an outcome test, link it, re-freeze).
4. The frozen proof bytes are unchanged and every proof passes at head (twice; journeys 3 times).

With an empty manifest and no changed map doc it checks CI and rename residue.
It writes its result to
`<git-common-dir>/omp-prove/<head>.result.json` and posts it on the PR. That file with
`status: pass` for the exact head is the prove pass signal. With dynaflows on, rails' `gh pr merge`
guard requires it, the structure result below and CI green on that head. A rebase or other branch
update requires both `prove()` and `structure_review()` on the new head before merge.
No review agent or status posting; re-freeze only for a discovery or a changed proof file.
Mechanics live in [verify-loop](references/verify-loop.md).

After `prove()`, main calls `await structure_review(repo, parent, head, target_ref=target_ref)`
on that same PR range ([contract](references/patterns.md#structure_review)). It extracts affected
folder states from Git objects, asks the eight structure/naming questions (prefix only for
eligible clusters), saves raw scores and updates one shadow findings comment. Provisional
decisions and infrastructure errors never affect `prove()` or merge. A `ready` decision with
a finding sets `halt`: rails refuse merge when this exact-head structure result is missing,
invalid or has `halt: true`. Both result files are required even with all decisions provisional.
See [ADR 0003](../../../../../docs/adr/0003-structure-checks-at-pr-diff-scope.md).

### 6. Integrate

The valid pathCheck-free schema-v2 record from stage 4 remains required; the Git no-CI exception
does not waive setup. Main never commits. Main follows
`skill://git-tool/references/merge-and-recover.md` for merge method, confirmation and recovery.
Git-tool owns Git authority: `agentMerge: disabled` requires owner approval.
Do not copy merge mechanics into playbooks. Before merge, main must have run both `prove()` and
`structure_review()` on the current exact PR head, with CI green, prove `status: pass` and structure
`halt: false`. Missing, invalid or wrong-head results block, even with an empty manifest.
Fix ready findings and rerun both helpers and CI on every new head before merging.

Recovery uses git-tool's revert mechanics, with an empty manifest when the revert changes no
behaviour. A revert still needs both exact-head helpers; if it changes a feature-map doc without
a linked frozen proof, `prove()` blocks. Escalate with those doc paths rather than waive the gate.
The durable fix gets its own PR with proofs reproducing the failure.

## References and evals

- Refactors: `skill://omp-dyna-workflows/references/delivery-principles.md`.
- Bugs: `skill://omp-dyna-workflows/references/delivery-principles.md`.
- Intermittent failures: `skill://test-guidance/references/flakes.md`.
- Owner correction: `skill://codify-omp-workflow-corrections`.
- Changing dynaflows itself: the design directive in [AGENTS.md](AGENTS.md).
- `orchestrate`: frame, delegate independent units, prove each unit.
- `workflowz`: native `workpool()` for independent fan-out.
- `jevify`: deterministic prefilter, frozen rubric, calibrated `judge()` or
  `judge_batch()` for single-state properties; read `skill://jev-actions`.

## Templates

Load in a standalone Python eval cell:
`%load ~/.omp/agent/extensions/dynaflows/skills/omp-dyna-workflows/templates/workflows.py`.
Only `freeze`, `prove`, `structure_review`, `blind_label`, `dependency_audit_findings` and
`triage_advisories` are supported orchestration APIs.
`blind_label` requires eval's `agent()`/`wait()` globals; `triage_advisories` needs `judge()`,
and async `structure_review` needs `judge()`/`judge_batch()`.
`freeze`, `prove` and `dependency_audit_findings` also run in plain Python. Other functions
are internal helpers, even when plain-Python callable.
Signatures, schemas, caps and failure handling have one home:
[Pattern contracts](references/patterns.md).
