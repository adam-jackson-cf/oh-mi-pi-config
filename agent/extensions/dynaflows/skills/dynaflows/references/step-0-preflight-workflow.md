# Preflight: switch, record and playbook

## Objective

Confirm dynaflows is on for the repo, resolve its record and select exactly one playbook before Frame.

## Required actions

1. Apply this skill only with dynaflows on. The repo's settings live in its record,
   `repos/<owner>/<name>/` under the oh-mi-pi-config root, named by the clone's lowercase `origin` remote;
   worktrees resolve like their main checkout. [The record contract](../../../../../lib/record/README.md) owns its files.
   The repo itself holds nothing about omp or dynaflows.
2. The record's `dynaflows.enabled` is the switch. No `origin` remote or no record means off.
   A machine default `~/.omp/dynaflows.json` of `{"enabled": false}` turns it off for every repo on that machine.
3. The owner turns it on or off in the main chat. A standalone directive line (case-insensitive) sets it at once:
   - on: `$dynaflows`, `$dynaflows` followed by an on verb (`on`, `enable`, `init`, `initialise`, `start`,
     `set up`, `turn on`), or an on verb before `$dynaflows`;
   - off: `$dynaflows off|disable|stop` or `disable|stop|turn off $dynaflows`.
   Two contradictory lines change nothing; ask which the owner means.
4. Judge any other wording, such as "I want to enable dynaflows": call the `dynaflows` tool only when the owner
   is asking for it, not asking about it, quoting it or negating it. Never call it on your own initiative.
5. Turning it on with no record starts [harness setup](harness-setup.md) from Step 0; write the record,
   with `dynaflows.enabled` true, yourself. A refused record resumes setup, and you repair the record in place.
   Write approval fields only as the owner stated them in the chat; never ask the owner to save a file.
6. When the prompt is only the switch ask, report the stage found and start it; with setup complete, ask for the task.
7. Select exactly one active playbook; it holds only deltas to the stage references:
   - evidence-only deliverable: [Investigation](investigation.md);
   - [Project setup](project-setup.md) is a prerequisite PR when controls are missing, when user-visible work has no
     Verification surface and feature map (`verification_surface.feature_map_path`) in the record's `profile.md`,
     or when the repo's checkers hold, or could hold, a harness check step's rules that the record has not listed
     in `guardrails.check.repo`;
   - multiple PRs or flagged delivery: [Multi-phase](multi-phase.md);
   - otherwise the primary deliverable selects [Bug fix](bug-fix.md), behaviour-preserving [Refactor](refactor.md)
     or [Feature](feature.md).
8. Harness-owned per-edit `path-check` never triggers setup through `guardrails.check.repo`: it always uses the
   record's required `guardrails.structure`, `guardrails.naming` and `guardrails.paths` settings.
   An owner-approved no-runnable-app surface exemption does not trigger setup; it declares no feature map,
   so `freeze()` accepts no proofs there.
9. An investigation report seeds Frame of a separately requested implementation; it is not permission to implement.
10. Route these requests on:
    - refactors and bugs: [delivery principles](delivery-principles.md);
    - intermittent failures: `skill://test-guidance/references/flakes.md`;
    - an owner correction: `skill://codify-omp-workflow-corrections`;
    - changing dynaflows itself: the design directive in [AGENTS.md](../../../AGENTS.md);
    - `orchestrate`: frame, delegate independent units and prove each unit;
    - `workflowz`: native `workpool()` for independent fan-out;
    - `jevify`: deterministic prefilter, frozen rubric, and calibrated `judge()` or `judge_batch()` for
      single-state properties; read `skill://jev-actions`.

## Done when

- The switch state, record path and its validity are known, or harness setup has started.
- Exactly one playbook is active, and any routed request has its reference read.
