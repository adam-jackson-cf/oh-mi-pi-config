# Preflight: switch, record and playbook

## Objective

Confirm the repo is in dynaflows structured mode, resolve its record and select exactly one playbook before Frame.

## Required actions

1. Apply this skill only in structured mode. The repo's settings live in its config,
   `.agentrails/config.json` in its main checkout, kept out of git;
   worktrees read their main checkout's file. [The config contract](../../../CONFIG.md) owns it.
   The repo itself holds nothing about omp or dynaflows.
2. The extension's `modes.json` selects the mode, keyed by the lowercase `origin` owner/name. An unlisted repo,
   no `origin` remote or, for structured mode, no valid record means minimal mode.
3. The owner switches the mode or opts into device rollout in the main chat; the rails apply `$dynaflows …` and
   `$rollout` lines themselves.
4. Asking for structured mode with no record lists the repo in `modes.json` and starts [harness setup](harness-setup.md)
   from Step 0; write the record yourself. A refused record resumes setup, and you repair the record in place.
   Write approval fields only as the owner stated them in the chat; never ask the owner to save a file.
5. When the prompt is only the switch ask, report the stage found and start it; with setup complete, ask for the task.
6. Select exactly one active playbook; it holds only deltas to the stage references:
   - evidence-only deliverable: [Investigation](investigation.md);
   - [Project setup](project-setup.md) is a prerequisite PR when controls are missing, when user-visible work has no
     Verification surface and feature map (`verification_surface.feature_map_path`) in the record's `profile.md`,
     or when the repo's checkers hold, or could hold, a harness check step's rules that the record has not listed
     in `guardrails.check.repo`;
   - multiple PRs or flagged delivery: [Multi-phase](multi-phase.md);
   - otherwise the primary deliverable selects [Bug fix](bug-fix.md), behaviour-preserving [Refactor](refactor.md)
     or [Feature](feature.md).
7. Harness-owned per-edit `path-check` never triggers setup through `guardrails.check.repo`: it always uses the
   record's required `guardrails.structure`, `guardrails.naming` and `guardrails.paths` settings.
   An owner-approved no-runnable-app surface exemption does not trigger setup; it declares no feature map,
   so `freeze()` accepts no proofs there.
8. An investigation report seeds Frame of a separately requested implementation; it is not permission to implement.
9. Route these requests on:
   - refactors and bugs: [delivery principles](delivery-principles.md);
   - intermittent failures: `skill://test-guidance/assets/templates/installed/references/flaky-tests.md`;
   - changing dynaflows itself: the design directive in [AGENTS.md](../../../AGENTS.md);
   - `orchestrate`: frame, delegate independent units and prove each unit;
   - `workflowz`: native `workpool()` for independent fan-out;
   - `jevify`: deterministic prefilter, frozen rubric, and calibrated `judge()` or `judge_batch()` for
     single-state properties; read `skill://jev-actions`.

## Done when

- The switch state, record path and its validity are known, or harness setup has started.
- Exactly one playbook is active, and any routed request has its reference read.
