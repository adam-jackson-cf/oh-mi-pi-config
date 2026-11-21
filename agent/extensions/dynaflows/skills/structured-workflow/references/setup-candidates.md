# Harness setup: Step 3 candidate deltas

## Objective

Draft the record from the harness, shadow-run repo-owned steps and prepare harness-free repo controls.

## Guidance

- Draft the record from the harness against a temporary clone of the target under the candidate root:
  `uv run --project <omp root>/agent/extensions/dynaflows/engine guardrails init --repo <temporary clone>
  --record <draft record dir>`. The draft record dir sits under the candidate root, never under the records directory;
  in extend mode, start it as a copy of the existing record.
- Never copy, adapt or edit the [engine](../../../engine/README.md) code, and never install it into the target's
  environment or lockfile.
- `init` discovers make targets, hook managers, CI jobs and (through `gh`) required checks, and drafts `gate.json` and
  `profile.md`. It writes nothing into the clone, never overwrites a recorded value, never writes an approval and never
  selects structured mode unless asked. It writes proposed native-tool configs (Ruff, mypy,
  import-linter) to the record's `proposed/` directory for the owner to apply as a repo fix, and proves language-server
  readiness with a canary `references` query.
- `--commit-command` and `--ci-command` name the repo's own gate commands; `--runner` and `--required-context` set the
  record's `ci` object. Init reports a missing `checks.commit` as `required:`.
- In adopt mode, shadow-run each `repo` or `grow` step on the same clone with `guardrails <step> --repo <temporary clone>
  --record <draft record dir>` and the record's `checks.commit`. Sort every finding the harness reports and
  `checks.commit` passes:
  - the repo checker lacks the rule, scopes it narrower, baselines it or misses files: add the fix to the step's
    `grow` PR;
  - the repo checker's own exception or setting accepts code that a stale record setting rejects: the repo checker is
    the authority, so drop the record setting;
  - never change repo code only to satisfy the harness copy.
- Zero findings on both sides shows no gap but not coverage; coverage comes from Step 2's rule-ID evidence.
  Draft each `grow` PR as candidate repo changes, with tests for every added rule.
- Give `repo` and `grow` steps to the repo with `guardrails init --settings` (`check` itself takes no options), for
  example `{"check": {"repo": {"python-quality": ["scripts/checks/check-python-quality.py"]}}}`. Leave out settings only
  owned steps read; `check` rejects `guardrails.python` once every configured Python step is the repo's.
- Fill every per-repo value in the draft `profile.md` front matter: tiers, layout, `structure`, scope exclusions,
  `guardrails.exceptions` (each with an owner-approved reason) and `guardrails.<checker>` settings. A behaviour the
  settings cannot express is a change to the engine, never a local fork. A test framework without an adapter follows
  [adapter authoring](adapter-authoring.md); for a stack without a reference, missing checkers are a harness change.
- Draft the required profile `guardrails.structure`, `guardrails.naming` and `guardrails.paths` settings for harness
  `guardrails path-check --repo <temporary clone> --record <draft record dir>`. It covers `STRUCT.dependency-direction`,
  `STRUCT.folder-placement`, `NAME.conventions` and `TEST.placement`. Rails supply changed paths as separate arguments;
  clean exits 0, failures exit nonzero with one error per line naming the file, rule ID and concrete fix.
  Never add a repo path-check script for the harness, or remove these settings because whole-repo checks are repo-owned.
- Test-evidence checks read normalised evidence: the adapter's JUnit run report and per-tier collect-only test IDs.
  The harness runs `guardrails report-check` and `collection-check`, and `budget-check` at the agent's commit only, with
  JUnit and temporary output outside the worktree. Name overrunning tests with a speed-up fix; never propose a tier move.
- `guardrails check` at commit (staged changes against HEAD) and `guardrails diff-check` (an explicit range) implement
  `TEST.growth.justified` (a descriptive behaviour name per new test function, the
  Python function name or JS/TS test title, with at least two non-generic alphabetic words; parameter rows share it)
  and `TEST.duplicate-path` (normalised call-path and assertion fingerprints).
- The other test-guidance rules follow each catalogue row's `reference`: `check` at commit and `diff-check` (coverage ratchet,
  golden change record, identity artefacts), `diff-check` (timeout increases), `python-test-hygiene` (subprocess timeouts), `guard-run` (user-data
  guard, fast-tier isolation), `surface-lint` and `lever-selftest` (verification surface), `profile-check`
  (`PROFILE.complete`), `adapter` (self-mocking, test hooks, evasion markers, property seeds) and, in T3 only,
  `mutation-check` after the adapter's `mutation-report`. Affected T2 selection uses
  `guardrails adapter <name> affected --base <ref>`, once per adapter the record declares, reading the record's
  Ownership areas. None is wired into the target.
- The repo-side controls (check-only full runner, CI wiring, commit target) default the required context to `gate`.
  For GitHub Actions, the parallel-CI aggregate runs with `if: ${{ always() }}`.
- Identify the superseded checks and config to remove, including any omp-specific files the repo holds.
