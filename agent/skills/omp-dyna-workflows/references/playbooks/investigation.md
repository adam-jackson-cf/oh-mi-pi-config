# Investigation playbook

## Objective

Answer the engineering question with traceable scratch-only evidence; deliver the spike report.

## Steps

1. **Frame**: define outcome, competing explanations, acceptance criteria and only questions
   whose answers change the recommendation; use `kiss` to cut criteria/questions.
   Read `skill://codegraph/references/readiness-and-bootstrap.md` before discovery and
   `skill://codegraph/references/query-selection.md` to trace owners and relevant flows.
2. **Spike**: read `skill://git-tool` and
   `skill://omp-dyna-workflows/references/patterns.md`; give questions pass/fail predicates,
   timeboxes and declared side effects. Shared-service writes need owner approval.
   Create a detached worktree outside the repo from default; non-isolated `task` uses absolute
   paths. All experiments are scratch-only; never edit the source checkout or publish code.
   Read `skill://codegraph/references/index-maintenance.md` for stale graphs,
   `skill://codegraph/references/mcp-registration.md` for missing registration and
   `skill://codegraph/references/fallback-and-handoff.md` for unavailable access; report source
   prerequisites rather than changing source state. Use the least experiment that distinguishes
   explanations; prototype races only when built results decide, council only when they cannot.
   Read `skill://jev-actions` for evals: deterministic first, Jev for single-state properties,
   bundled cross-family `reviewer` for contextual critique. Deliver `local://spike-<slug>.md`
   with answers, commands, outputs, failures, remaining uncertainty and recommendation;
   remove the detached worktree. The report is the deliverable; stop after stage 2.
3. **Plan / Implement / Verify / Integrate**: not applicable to this investigation.
   Route requested implementation to its own code-changing playbook, never reuse spike code.
