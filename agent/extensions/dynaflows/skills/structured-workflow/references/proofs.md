# Dynaflows proofs

## Binding policy and profile

- In dynaflows structured mode, the testing guidance [test-guidance](skill://test-guidance) installed in the
  repository is binding. The profile may tighten, never relax, it.
- The profile is the record's `profile.md`: `~/.omp/repos/<owner>/<name>/profile.md`, keyed by origin,
  gitignored and never committed. Never look for a repo-local profile or name a harness directory
  inside the repo. Repo paths in settings are repo-relative.
- Missing profile or surface values for user-visible work route to the active playbook's Project
  setup prerequisite. Hand off missing values; resume dependent implementation only after its
  owner-approved prerequisite PR completes. Rejection leaves it blocked. A valid surface exemption
  does not trigger setup.
- The profile's JSON front matter is authoritative for `freeze()` and `guardrails profile-check`
  (`PROFILE.complete`); keep prose in step. Agents never write the repo config; approvals come only from
  the owner's lines. Git-tool's
  [config authority](skill://git-tool/references/authority-and-safety.md#who-writes-the-config)
  applies independently of dynaflows.
- The template's `structure` supplies capability owners, tiers and import direction;
  `house_style` supplies the installed version; `guardrails` supplies per-checker settings
  and owner-approved `exceptions` (`rule`, `path` or glob, `reason`, optional `check`).
  `structure.tiers` and `guardrails.exceptions` may be empty.  Checker module documentation defines per-checker settings.
- During approved setup, implementation writes the feature map in the repo and profile/guardrail
  settings in the record. Hand mapping gaps to the approved setup extension. Owner-approved budget
  edits belong to implementation, not the frozen proof task; first limits and raises require the
  owner's stated decision and per-machine reference.
- Rails run the record's `checks.commit`, then harness checks, including the staged-change diff rules in
  `guardrails check`, `report-check` and `budget-check` on the fast-tier report. CI runs the CI command only and never measures test timing.
  Never bypass these checks or repo hooks. Each test rule's finding prints its own remediation.

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
  head being merged. Doctor failures, hangs and
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
- Discoveries: when implementation finds user-facing behaviour the plan did not name (a new error, option,
  output or state the caller can see), the implementation task adds or updates its map entry and writes its
  outcome test, leaves both uncommitted with no other pending edits, and stops. Main re-freezes
  under [freeze](freeze.md#re-freeze-for-discoveries), then implementation resumes.
  There is no added-after-freeze exception.
- Map sweep: before the PR opens, a sweep task lists every user-facing behaviour the branch diff adds or
  changes, each with a concrete source path; an item without one is dropped. Each item no map document
  changed in the diff covers gets a map entry and an outcome test, handled as a discovery (re-freeze).
  The sweep writes map entries and tests only: no review findings, finding schema or reviewer step.

## Exempt surfaces

- A valid no-runnable-app exemption uses outcome tests instead of journeys: T1 in-process or T2 for
  actual host-process seams. Never invent a lever for an exempt repo.
- An exempt surface declares no feature map, so `freeze()` accepts no proofs there.
  Raise an exempt-repo PR needing TDD proofs with the owner.
- Nightly drift requires both a feature map and Run events scheduler; otherwise report the valid
  exemption, or the missing profile value, rather than implying a run occurred.
