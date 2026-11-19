# Harness setup: Step 2 rule-mapping deltas

## Objective

Map every catalogue rule to its owner and give every check step exactly one outcome.

## Guidance

- The contract is every entry in `agent/extensions/dynaflows/engine/guardrails/data/guardrail-rules.json`, including
  the language-neutral additions. Treat each ID, semantic, threshold and target-derived requirement as binding.
- Map `STRUCT.dependency-direction` to a layer-edge allowlist and `STRUCT.folder-placement` to path ownership.
  Map `NAME.conventions` to path and symbol patterns and `TEST.placement` to owner and tier paths from the record's
  `profile.md`, using its ownership areas and affected-T2 selection. Unmapped T2 files fail; unknown changed-code
  ownership selects all T2.
- Map `TEST.budget.fast-tier-time` and `TEST.budget.per-test-time` to a checker consuming the normalised JUnit run
  report. Limits are selected by a deterministic local machine probe from the record's `profile.md` and derived from at
  least five baseline runs. Timing blocks only at the agent's commit; `checks.ci` never runs the budget checker.
  The catalogue marks both rules `git_hook` evidence. Missing, invalid or stale reports are fatal.
  First limits and raises require owner approval at Step 5; unknown machines report timing only and prompt to record
  a baseline.
- Map `TEST.growth.justified` to a base-ref diff requiring every new test function to name the behaviour it proves;
  a generic or placeholder name fails with the test name and the rename-or-remove fix. Map `TEST.duplicate-path` to
  normalized call-path and assertion fingerprints compared against existing and other new cases; duplicates fail
  naming both tests and the merge-or-remove fix.
- Map `GATE.local-ci-parity` and `GATE.pre-commit` to the record's `gate.json` command arrays (`checks.ci`,
  `checks.commit`), each naming the repo's own command, never an omp path. Record the repo's actual local, CI and hook
  invocations and failure propagation, and any existing pre-push hook as a practice to replace.
- Map `GATE.path-check` to harness `guardrails path-check --repo <worktree> --record <record dir>`, never a record
  command. Rails run it after edits and before commit; at commit `checks.commit` and the whole-repo harness checks
  follow. A repo hook remains the repo's choice.
- A row with a `reference` field names the `guardrails` check step that enforces it from the harness. Give every check
  step exactly one outcome from Step 1's rule-ID evidence:
  - `repo`: checkers that `checks.commit` runs enforce every rule ID the step reports, list files from git and apply
    no baseline. Map the step to its checker files in `guardrails.check.repo`; one step's rule IDs may be split across
    several checkers.
  - `grow`: the repo can hold the step's rules (its own checker scripts, a configurable tool or its test runner, run by
    `checks.commit`), but a rule ID is absent, a checker applies a baseline, walks the disk or is not run by
    `checks.commit`. The fix is a repo PR in the repo's own style and with its own tests that adds the missing rules,
    fixes every baselined finding and deletes the baseline, or wires the checker in. Once it merges, the step is `repo`.
  - `harness`: the repo cannot hold the step's rules without depending on omp or dynaflows (`profile-check` checks the
    record itself), or it has no checker capability yet (a greenfield repo).
- `SECURITY.dependency-audit` has no check step and never the `harness` outcome, even in a greenfield repo.
  It is `repo` when a checker that `checks.commit` and `checks.ci` run meets the catalogue row's contract for every
  lock file and manifest in Step 1's map; otherwise it is `grow`, and a repo with no audit gets one.
  The repo's exception file goes into the record's `ownerOnlyPaths`.
- Never run a harness step beside a repo checker, even until a repo PR lands, and never offer a route where the owner
  declines a `grow` PR and the harness step runs instead.
- `guardrails`' own tests prove the harness rules once for every repository; the repo's tests prove the repo's checkers.
- A baseline of waived findings in the repo's own tools for catalogue rules without a `reference` (Ruff, mypy and
  similar) is also a `grow` fix, never an accepted state. mypy follows every import (`MYPY.follow_imports`).
- An applicable rule with neither a repo home nor a `guardrails` subcommand is implemented as a repo checker when the
  repo has the capability, otherwise as a change to `guardrails` in oh-mi-pi-config with its own tests;
  keep the rule blocked until that change lands.
