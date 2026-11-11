# Project setup playbook

Establish the approved verification surface before guardrails in a prerequisite PR.
Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order.
Helper mechanics live in [patterns](../patterns.md); Git mechanics in `skill://git-tool`.

## 1. Frame

Identify stack, protected work, missing controls and owners. Read `skill://setup-repository-guardrails`
and `skill://test-guidance/references/verification-surface.md`.
Read `.omp/test-profile.md` when present. Trigger surface setup lazily for user-visible work
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
guardrails, not lever bytes. Project setup freezes before stage 4 records `dynaflowsContract`, so
this flag tolerates a missing gate or an absent or lower recorded contract; a higher one still refuses.
Run journey proofs several times before freezing. Complete guardrails Step 5 owner approval.
Surface-only mode still runs Steps 0–7 with extend scope: `TEST.placement`,
`STRUCT.folder-placement`, `NAME.conventions`, ownership areas and affected-T2 selection.
Keep the Step 5 STOP; existing controls are not permission to skip approval.

## 4. Implement

After freeze, implementation writes the feature map and copies
`skill://test-guidance/assets/templates/test-profile.md` to `.omp/test-profile.md`, filling its
machine-readable front matter and prose before guardrails application. `omp-guardrails init`
writes the same template; `omp-guardrails profile-check` must pass (PROFILE.complete).
Init also records the current dynaflows contract version as `.omp/gate.json` `dynaflowsContract`
(`--greenfield` writes the gate; `--extend` raises a stale one and needs the profile written
first); commit it with the setup so `prove()` sees the current version at head.
Preserve existing profile values when extending it.
Use `git check-ignore .omp/test-profile.md`; if ignored, re-include that exact path in the
repo ignore rules and confirm it is trackable. Never force-add an ignored profile.
Then invoke guardrails Step 6 using the profile and Step 7 coverage verification.
Install the approved hook before the first commit; commit frozen proofs and greening changes
atomically through it. Bootstrap permits proposing controls before they exist, not bypassing approval.

## 5. Verify

Use canary/live evidence under the shared contract; reject doctor failures as proof assertions.

## 6. Integrate

Complete this prerequisite PR before returning to Frame of the original request.
