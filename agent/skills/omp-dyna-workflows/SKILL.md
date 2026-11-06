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

- **Dynaflows**: this skill applies only with dynaflows on. Owner toggles: `$dynaflows` /
  `$dynaflows on`, `$dynaflows off`. Resolution: `<repo>/.omp/dynaflows.json` >
  `~/.omp/dynaflows.json` > off. Dynaflows off: ignore this skill.
- **Purpose**: Route every non-trivial engineering request to one executable playbook.
- **When**: Features, bug fixes, refactors, investigations, project setup, or multi-phase work.
- Match the request to exactly one playbook below. Execute stages 1–6 in order:
  1. **Frame**: outcome, acceptance criteria, and only questions whose answers change the plan.
     Use `kiss` to cut criteria and questions, not to review risk.
  2. **Spike**: only for remaining questions; declare predicates and side effects first.
     Answer on the real surface in a detached scratch worktree; discard all spike code.
  3. **Plan**: map every criterion to a named proof in a frozen JSON manifest. Missing proof
     surfaces are the first tasks. Keep the plan draft until the spike finishes; native plan
     mode is not the default. Request plan review only when the owner asks to approve it.
  4. **Implement**: branch `agent/<slug>` from default, never from the spike. Spawn `task`
     for proofs first; main records proof-file hashes (re-freeze); separately spawn `task`
     for implementation. Every landed commit stays green; parent overlays prove red. Commits
     run `checks.commit` through the pre-commit hook; never bypass it.
  5. **Verify**: run deterministic `prove()`, then its cross-family `verifier` drives every
     criterion live, checks affected doc lines, and reports diff scope/coverage.
  6. **Integrate**: CI (`checks.ci`) green on head, rebase-merge with `--match-head-commit`,
     wait for default CI.
- Prerequisite to implementation: `.omp/gate.json` provides `checks.commit`, `pathCheck` and an
  installed pre-commit hook. Missing any: ask the owner before guardrail work. On yes, run
  `skill://setup-repository-guardrails`; write `.omp/guardrail-proposal.json`
  `{"schemaVersion":1,"minimal":[paths],"full":[paths]}` (repo-relative). `minimal`: least for
  `checks.commit`, `pathCheck` and the hook. `full`: every recommended guardrail. Then `ask`:
  question contains "guardrail", options "Minimal" and "Full". agent-rails blocks
  `.omp/gate.json` and proposal paths until answered; timeout auto-selection is not approval;
  the unchosen option's paths stay blocked.
- Delegate every code change, including small fixes, to `task`. Each brief lists the exact
  reference URLs needed by its steps and requires reading them before the first edit.
- Briefs must give absolute worktree paths; agent-rails rejects relative or symlink-escaping
  writes from subagents.
- Where this skill is silent, preserve existing content and make no irreversible write;
  ask once only when the choice changes the outcome, and state the choice in the result.
- Runbook: [Feature](references/playbooks/feature.md).
- Runbook: [Bug fix](references/playbooks/bug-fix.md).
- Runbook: [Refactor](references/playbooks/refactor.md).
- Runbook: [Investigation](references/playbooks/investigation.md).
- Runbook: [Project setup](references/playbooks/project-setup.md).
- Runbook: [Multi-phase](references/playbooks/multi-phase.md).

## Index

- **Purpose**: Load only the guidance required by the current situation.
- **When**: A playbook reaches the corresponding action.
- Read hidden skills explicitly through these URLs; a project's own test skill takes precedence.

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
| Spike recipe | skill://omp-dyna-workflows/references/patterns.md |
| Proof manifest and freeze | skill://omp-dyna-workflows/references/patterns.md |
| prove() and verifier | skill://git-tool/references/verify-loop.md |
| Git verification and risk tiers | skill://git-tool/references/verify-loop.md |
| Merge or recover red main | skill://git-tool/references/merge-and-recover.md |
| Owner corrects repeated mistake | skill://codify-omp-workflow-corrections |
| Evals | skill://jev-actions |

## Use the native keywords first

- `orchestrate`: frame the outcome, delegate independent units, prove each unit.
- `workflowz`: native `workpool()` for independent fan-out; dependency-coupled templates below.
- `jevify`: freeze the rubric before data, filter deterministically, judge in bulk, inspect flags.

## Owner rules on top

- **Independent sessions.** A verifier, reviewer or second labeller has none of the author's
  context; each `agent()` spawn qualifies. Never let an author's session judge its own work.
- **Cross-family verifier.** The verifier family must differ from every author family of the
  change; agent-rails enforces this at spawn. Sol authors use Sonnet 5.5 medium verification.
  An override must preserve that invariant, including designer-authored changes.
- **Evals: deterministic, then Jev, then a cross-family reviewer.**
  1. Run the real check command, pre-filter or rule first.
  2. Use Jev only for properties observable in one state. Read `skill://jev-actions`; use
     `judge()` or `judge_batch()` behind a cheap prefilter and calibrate on labelled samples.
  3. Intent, appropriateness, relevance, context-dependent questions and open-ended critique
     go to the bundled `reviewer` in an independent cross-family session, not to Jev.
- **Caps that raise.** `MAX_AGENTS` is 16 per wave; templates raise `WorkflowError`, never drop work.

## Templates

Load in a standalone Python eval cell:
`%load ~/.omp/agent/skills/omp-dyna-workflows/templates/workflows.py`.
`agent()` and `wait()` are eval globals; deterministic functions also run in plain Python.

- `prove(repo, parent, head, manifest_path, manifest_sha256, objective, sensitive=None,
  max_checks=MAX_AGENTS)`: frozen proofs in detached parent/head worktrees, green twice at
  head, parent overlay red-for-reason or preservation, gate/sensitive scans, then one read-only
  cross-family verifier. Runs no repository checks (commit hook and CI own them). Return
  `status` (`pass`, `fail`, `needs_owner`), `deterministic`, `verifier`, `blocking`,
  `followUps`; remove worktrees on exit.
- `blind_label(batch_files, rubric_path, labels, labellers=("scout", "task"))`: two blind,
  read-only, cross-family sessions per frozen batch; return `agreed` and `disputed`.
  The orchestrator settles disputes; a human confirms before labels are written.

Contracts, manifest schema, spike recipe and failure handling: `references/patterns.md`.
