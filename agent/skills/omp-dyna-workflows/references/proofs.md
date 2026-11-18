# Dynaflows proofs

## Binding policy and profile

- With dynaflows enabled, [test-guidance](skill://test-guidance) is binding and overrides repo-local
  test skills and AGENTS.md test rules during coexistence. The profile may tighten, never relax, it.
- The profile is the record's `profile.md`: `~/.omp/repos/<owner>/<name>/profile.md`, keyed by origin,
  gitignored and never committed. Never look for a repo-local profile or name a harness directory
  inside the repo. Repo paths in settings are repo-relative.
- Missing profile or surface values for user-visible work route to the active playbook's Project
  setup prerequisite. Hand off missing values; resume dependent implementation only after its
  owner-approved prerequisite PR completes. Rejection leaves it blocked. A valid surface exemption
  does not trigger setup.
- The profile's JSON front matter is authoritative for `freeze()` and `omp-guardrails profile-check`
  (`PROFILE.complete`); keep prose in step. Agents write records directly, with approval fields only
  as the owner stated in chat. Git-tool's
  [record authority](skill://git-tool/references/merge-and-recover.md#who-writes-records)
  applies independently of dynaflows.
- The template's `structure` supplies capability owners, tiers and import direction; `house_style`
  supplies the installed version and ADR-backed exceptions; `guardrails` supplies per-checker
  settings such as structure, naming and paths. `structure.tiers` may be empty for flat code;
  `house_style.exceptions` may be empty. Each exception needs an ADR reference.
  Checker module documentation defines per-checker settings.
- During approved setup, implementation writes the feature map in the repo and profile/guardrail
  settings in the record. Hand mapping gaps to the approved setup extension. Owner-approved budget
  edits belong to implementation, not the frozen proof task; first limits and raises require the
  owner's stated decision and per-machine reference.
- Rails run the record's `checks.commit`, then harness checks, including `report-check` and
  `budget-check` on the fast-tier report. CI runs the CI command only and never measures test timing.
  Never bypass these checks or repo hooks. See [test enforcement](test-enforcement.md).

## Freeze and implementation ownership

- A proof is an outcome test or deterministic lever journey linked from the feature map.
  Every proof fails at base and passes at head: assert a broken or newly requested outcome, not
  infrastructure failure. Live paid/LLM calls and T3 exploration are never frozen proofs.
- Freeze pins whole proof-file bytes before implementation. Prefer extending an existing scenario
  except where frozen scope requires a dedicated file; the Bug fix playbook requires a small
  dedicated file. Implementation must never edit frozen test or lever bytes.
- Proof commands run journey files through the test runner. The proof task writes required lever
  changes and freezes them with the journey files. The greening implementation changes product code
  and feature-map prose, plus discovery tests before re-freeze.
- `prove()` checks journey stability: a `journey: true` proof must pass every clean run on the exact
  head being merged. Do not rerun journeys by hand before freeze. Doctor failures, hangs and
  contaminated state never count as clean runs or red-at-base evidence. Report blocked infrastructure
  and retain marker/probe evidence; repair only within authorized scope, otherwise ask the owner once
  whether to repair or leave the proof blocked. Resume only after the cause is repaired and doctor
  succeeds; never retry just for green.

## Map-linked proofs and discoveries

- Plan the new feature's intended map entry from its requirement before writing its proof.
  The map must exist before the first TDD proof, through Project setup.
- The proof task writes or updates the map entry linking each proof file. `freeze()` refuses any
  proof not linked by a map document under `verification_surface.feature_map_path`, and refuses all
  proofs if no map path is declared.
- Map documents are not frozen: implementation may refine their prose. At the PR, `prove()` blocks
  every changed map document that links no frozen proof.
- User-facing behaviour discovered during implementation gets a map entry and an outcome test from
  implementation, then a re-freeze. Its test is red at base because the behaviour did not exist there.
  There is no added-after-freeze exception.
- Before the PR, sweep every user-facing behaviour the diff adds or changes, citing a concrete source
  path for each. An item without a source path does not count. Any item no changed map document covers
  gets a map entry and a proof. The sweep produces map entries and tests, never review findings.

## Exempt surfaces

- A valid no-runnable-app exemption uses outcome tests instead of journeys: T1 in-process or T2 for
  actual host-process seams. Never invent a lever for an exempt repo.
- An exempt surface declares no feature map, so `freeze()` accepts no proofs there.
  Raise an exempt-repo PR needing TDD proofs with the owner.
- Nightly drift requires both a feature map and Run events scheduler; otherwise report the valid
  exemption, or the missing profile value, rather than implying a run occurred.
