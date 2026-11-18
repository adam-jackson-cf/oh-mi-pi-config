# Project setup playbook

Establish the approved verification surface and the repo's record before guardrails, with any
repo fixes in a prerequisite PR. The record (`repos/<owner>/<name>/` under the oh-mi-pi-config
root) is local and never committed; the PR holds only repo changes that never mention omp.
Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order.
Helper mechanics live in [patterns](../patterns.md) and [verify-loop](../verify-loop.md);
Git mechanics in `skill://git-tool`.

## 1. Frame

Identify stack, protected work, missing controls and owners. Read `skill://setup-repo-proofs`
and `skill://test-guidance/references/verification-surface.md`.
Read the record's `profile.md` when present. Trigger surface setup lazily for user-visible work
without a surface; an owner-approved no-runnable-app exemption does not trigger it.
Pick the setup skill's Step 0 modes from the starting point; more than one can apply:

- no record and no repo checkers: `greenfield`;
- repo checkers or tools that enforce, or could hold, harness rule families the record does not
  yet give them in `guardrails.check.repo`, with or without a record: `adopt`. Missing rules are
  added to the repo's checkers by a repo PR. Never reshape repo code to satisfy the harness copy
  of a rule the repo's checker carries;
- a record that lacks only a part, such as the verification surface: `extend`.

Never build a second guardrail system beside the repo's own; the
[design directive](../../AGENTS.md) holds the principles.
The harness-owned per-edit `path-check` is the explicit exception in
[ADR 0002](../../../../../docs/adr/0002-harness-owns-path-checks.md); it is not delegated through `guardrails.check.repo`.

## 2. Spike

Build a scratch lever and journey; execute a deliberately broken variant to show the intended
failure and a doctor/environment failure to show it cannot count as red. Save evidence,
never reuse spike code. Inventory guardrails using Steps 0–2, candidates Step 3 and dry-run
Step 4. Scratch work prepares Step 5 approval; never apply controls during inventory.

## 3. Plan

The feature map must exist before any TDD proof: `freeze()` refuses every proof when the record
profile declares no `verification_surface.feature_map_path`, and refuses a proof file no map doc
links. Setup therefore establishes the map first.

Proofs follow the one rule: red at base, green at head. A lever journey is red at base
because the lever is absent; a new-control canary is red at base because the violation is
still accepted there. Cover intentional violations rejected and valid inputs accepted.
A setup PR that changes no behaviour (config, docs, repo fixes or the first feature map only)
freezes an empty manifest with `freeze(..., project_setup=True)`, which tolerates an absent
record. Its PR check is CI green: the record is saved with `dynaflows.enabled` false, and
`prove()` would block the new map docs because they link existing tests, not frozen proofs.
A setup PR that freezes lever journeys or canaries needs the saved record with
`feature_map_path` before freeze: the proof task writes the lever, the runner-invoked journey
files and the map entries that link them, and main freezes them together.
Implementation owns the map's prose, profile and guardrails, not lever bytes.
Complete guardrails Step 5 owner approval.
Surface-only mode still runs Steps 0–7 with extend scope: `TEST.placement`,
`STRUCT.folder-placement`, `NAME.conventions`, ownership areas and affected-T2 selection.
Keep the Step 5 STOP; existing controls are not permission to skip approval.

## 4. Implement

Bootstrap may propose controls before a record exists; before ordinary implementation resumes,
save a valid pathCheck-free schema-v2 `gate.json` with `checks.commit` and a complete path-check
profile. Obsolete `checks.pathCheck` is invalid. When setup has proofs, complete the proof task
and main's red-at-base freeze before the separate implementation task; otherwise main freezes
the empty bootstrap manifest first. Main never commits.

Implementation writes or completes the feature map in the repo: one document per feature under
`feature_map_path`, each linking the outcome tests or journeys that prove it
(`skill://test-guidance/references/verification-surface.md`). The confirmed record's
`profile.md` starts from `skill://test-guidance/assets/templates/test-profile.md`, with its
machine-readable front matter and prose filled; `omp-guardrails init`, run from the harness with
`--record <harness>/repos/<owner>/<name>`, drafts the same template and the record's `gate.json`.
`omp-guardrails profile-check` must pass (PROFILE.complete).
The profile requires `guardrails.structure`, `guardrails.naming` and `guardrails.paths` for
harness `omp-guardrails path-check --repo <worktree> --record <record dir>`. Keep repo CI commands unchanged.
Preserve existing profile values when extending it. Write the live record yourself; never ask the
owner to save a file. Set `dynaflows.enabled` true only when the owner asked for dynaflows in the chat
(`omp-guardrails init --enable-dynaflows`), else false. Write approval fields only as the owner stated
them in the chat, and retain the Step 5 approval STOP before applying repository fixes.
Never commit, stage or copy a record into the repo.
Then invoke guardrails Step 6 and Step 7 verification. Commit frozen proofs and greening changes
atomically; rails run harness `path-check` with record config, then `checks.commit` and the
whole-repo harness checks at each commit.
Bootstrap permits proposing controls before they exist, not bypassing approval.

## 5. Verify

With dynaflows enabled, the completed valid pathCheck-free schema-v2 record remains required:
main runs `prove()` then `structure_review()` on the exact PR head. Frozen journeys and canaries
must pass and every changed map doc must link one; doctor failures are never proof assertions.
The no-behaviour bootstrap PR described in Plan leaves dynaflows off and uses git-tool's
exact-head CI gate instead; this does not authorize ordinary implementation before setup completes.

## 6. Integrate

With dynaflows enabled, require the valid stage-4 record, exact-head CI green, prove `status: pass`
and structure `halt: false`; missing, invalid or wrong-head results block, even for empty manifests.
Rerun both helpers after every head change. Main never commits.
Complete this prerequisite PR before returning to Frame of the original request.
