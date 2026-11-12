# Project setup playbook

Establish the approved verification surface and the repo's record before guardrails, with any
repo fixes in a prerequisite PR. The record (`repos/<owner>/<name>/` under the oh-mi-pi-config
root) is local and never committed; the PR holds only repo changes that never mention omp.
Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order.
Helper mechanics live in [patterns](../patterns.md); Git mechanics in `skill://git-tool`.

## 1. Frame

Identify stack, protected work, missing controls and owners. Read `skill://setup-repository-guardrails`
and `skill://test-guidance/references/verification-surface.md`.
Read the record's `profile.md` when present. Trigger surface setup lazily for user-visible work
without a surface; an owner-approved no-runnable-app exemption does not trigger it.
For an already guarded repo, use surface-only extend mode, not a second guardrail system.

## 2. Spike

Build a scratch lever and journey; execute a deliberately broken variant to show the intended
failure and a doctor/environment failure to show it cannot count as red. Save evidence,
never reuse spike code. Inventory guardrails using Steps 0–2, candidates Step 3 and dry-run
Step 4. Scratch work prepares Step 5 approval; never apply controls during inventory.

## 3. Plan

Map lever journeys to `preserve` proofs; new-control canaries remain `new-surface`, existing
controls `preserve`. Cover intentional violations rejected and valid inputs accepted.
The proof task writes the production lever and runner-invoked journey files and freezes them
 together with `freeze(..., project_setup=True)`; implementation owns the feature map, profile and
guardrails, not lever bytes. Project setup freezes before stage 4 writes the record's
`dynaflowsContract`, so this flag tolerates a missing record gate or an absent or lower contract; a
higher one still refuses. A stale contract alone in a repo that already has controls is not a
project-setup trigger: it takes the record-only `omp-guardrails init --extend` draft in
[SKILL](../../SKILL.md#4-implement).
Complete guardrails Step 5 owner approval.
Surface-only mode still runs Steps 0–7 with extend scope: `TEST.placement`,
`STRUCT.folder-placement`, `NAME.conventions`, ownership areas and affected-T2 selection.
Keep the Step 5 STOP; existing controls are not permission to skip approval.

## 4. Implement

After freeze, implementation writes the feature map in the repo. The confirmed record's
`profile.md` starts from `skill://test-guidance/assets/templates/test-profile.md`, with its
machine-readable front matter and prose filled; `omp-guardrails init`, run from the harness with
`--record <draft dir>` outside `repos/`, drafts the same template and the record's `gate.json`,
including the current `dynaflowsContract` (written when absent, raised when stale, never lowered).
`omp-guardrails profile-check` must pass (PROFILE.complete).
Preserve existing profile values when extending it. After Step 5 approval, ask the owner to save
the draft as the live record by hand, with `dynaflows.enabled` false (agents never write records);
never commit, stage or copy it into the repo.
Then invoke guardrails Step 6 and Step 7 verification. Commit frozen proofs and greening changes
atomically; rails run the record's `checks.commit` and the harness checks at each commit.
Bootstrap permits proposing controls before they exist, not bypassing approval.

## 5. Verify

Use canary/live evidence under the shared contract; reject doctor failures as proof assertions.

## 6. Integrate

Complete this prerequisite PR before returning to Frame of the original request.
