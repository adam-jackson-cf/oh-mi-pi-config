# Project setup playbook

## Objective

Establish the approved verification surface and the repo's record before guardrails, with repo fixes in a prerequisite PR.

## Stage deltas

- Run [generic setup](skill://setup-repo-proofs) with [harness setup](harness-setup.md) deltas at each step;
  it is not a second setup system.
- Records are local and never committed. The PR holds only independent repo changes that never mention omp.
- Frame: identify the stack, protected work, missing controls and owners. Read `skill://setup-repo-proofs`,
  `skill://test-guidance/references/verification-surface.md` and the record's `profile.md` when present.
- Frame: trigger surface setup lazily for user-visible work without a surface; an owner-approved no-runnable-app
  exemption does not trigger it. Pick the setup modes as [harness setup](harness-setup.md#step-0-safety-and-modes) defines.
- Spike: build a scratch lever and journey. Execute a deliberately broken variant to show the intended failure, and a
  doctor/environment failure to show it cannot count as red. Save the evidence; never reuse spike code.
- Spike: inventory guardrails with setup Steps 0–2, candidates with Step 3 and the dry run with Step 4.
  Scratch work prepares Step 5 approval; never apply controls during inventory.
- Plan: the feature map must exist before any TDD proof, because `freeze()` refuses every proof when the record
  profile declares no `verification_surface.feature_map_path`. Setup establishes the map first.
- Plan: a lever journey is red at base because the lever is absent; a new-control canary is red at base because the
  violation is still accepted there. Cover intentional violations rejected and valid inputs accepted.
- Plan: a setup PR that changes no behaviour (config, docs, repo fixes or the first feature map only) freezes an empty
  manifest with `freeze(..., project_setup=True)`, which tolerates an absent record. Its PR check is CI green: the
  record is saved with `dynaflows.mode` minimal, and `prove()` would block the new map docs because they link
  existing tests, not frozen proofs.
- Plan: a setup PR that freezes lever journeys or canaries needs the saved record with `feature_map_path` before
  freeze. The proof task writes the lever, the runner-invoked journey files and the map entries that link them,
  and main freezes them together. Implementation owns the map's prose, the profile and guardrails, not lever bytes.
- Plan: surface-only mode still runs setup Steps 0–7 with extend scope: `TEST.placement`, `STRUCT.folder-placement`,
  `NAME.conventions`, ownership areas and affected-T2 selection.
- Plan: keep the setup Step 5 owner-approval STOP; existing controls are not permission to skip approval.
- Implement: bootstrap may propose controls before a record exists, never bypass approval. Before ordinary
  implementation resumes, save the valid record that [Implement](step-4-implement-workflow.md) requires.
  Without proofs, main freezes the empty bootstrap manifest first.
- Implement: write or complete the repo's feature map, one document per feature under `feature_map_path`, each
  linking the outcome tests or journeys that prove it.
- Implement: write the record under [setup cutover](setup-cutover.md#step-6-authority-cutover), then run setup
  Steps 6 and 7. Commit frozen proofs and greening changes atomically.
- Verify: in structured mode, frozen journeys and canaries must pass and every changed map doc must link one;
  doctor failures are never proof assertions.
- Verify: the no-behaviour bootstrap PR leaves dynaflows in minimal mode and uses git-tool's exact-head CI gate instead;
  the owner merges it, because minimal mode refuses `gh pr merge`.
  This does not authorize ordinary implementation before setup completes.
- Integrate: in structured mode, the full merge gate applies even for empty manifests.
  Complete this prerequisite PR before returning to Frame of the original request.
