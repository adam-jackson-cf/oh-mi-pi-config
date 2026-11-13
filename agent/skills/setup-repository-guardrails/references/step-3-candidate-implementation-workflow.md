# Candidate implementation

## Objective

Draft the record and the candidate repo fixes outside the target checkout.

## Guidance

1. Draft the record from the harness; author an adapter if the framework has
   none. Where the stack has a skill reference (Python:
   [`omp-guardrails`](../assets/python-reference/README.md)), never copy,
   adapt or edit its code, and never install it into the target's environment
   or lockfile. Run it from the harness against a temporary clone of the
   target under the candidate root:
   `uv run --project <omp root>/agent/skills/setup-repository-guardrails/assets/python-reference
   omp-guardrails init --repo <temporary clone> --record <draft record dir>`.
   `<draft record dir>` sits under the candidate root, never under the records
   directory; in extend mode, start it as a copy of the existing record. `init`
   discovers the repo's make targets, hook managers, CI jobs and (through
   `gh`) required checks, and drafts the record's `gate.json` and `profile.md`
   from them. It writes nothing into the clone, never overwrites a recorded
   value, never writes an approval and never turns dynaflows on, so a second
   run changes nothing. It writes proposed native-tool configs to the record's
   `proposed/` directory for the owner to apply to the repo as a fix, and
   proves language-server readiness with a canary `references` query.
   `--commit-command`, `--path-command` and `--ci-command` name the repo's own
   gate commands; `--runner` and `--required-context` set the record's `ci`
   object. Init reports a missing `checks.commit` or `pathCheck` as
   `required:`, because a record without either does not load. In adopt
   mode, shadow-run each harness step a repo checker covers on the same
   clone: `omp-guardrails <step> --repo <temporary clone> --record <draft
   record dir>` and the record's `checks.commit`. A harness finding that
   `checks.commit` passes marks the repo checker weaker (a baseline, a
   narrower scope, a missing rule or a disk walk): draft its expansion as a
   candidate repo fix and keep the harness step until it lands. When they
   agree, give the step to the repo with `--settings`, for example
   `{"check": {"repo": {"python-quality": "scripts/checks/check-python-quality.py"}}}`,
   and leave out settings only owned steps read (`check` rejects
   `guardrails.python` once every configured Python step is the repo's).
   Then fill every per-repo value in the front matter of the draft
   `profile.md`: tiers, layout, `structure`, scope exclusions,
   `house_style.exceptions` (each with an ADR) and `guardrails.<checker>`
   settings. A behaviour the settings cannot express is a change to the
   reference itself, never a local fork. If the target's test framework has no
   adapter, follow [adapter authoring](adapter-authoring.md). For a stack
   without a reference, the missing checkers are a change to the harness, not
   to the target (Step 2).
2. The record's `pathCheck` (required) names the repo's own path command;
   when the repo has none, the candidate fixes add one. The setup always drafts
   both `pathCheck` and `checks.commit`. The harness's `omp-guardrails path-check` covers
   `STRUCT.dependency-direction`, `STRUCT.folder-placement`,
   `NAME.conventions`, and `TEST.placement` from record settings. A path
   command accepts changed paths appended as separate arguments, exits 0 when
   clean, and otherwise exits nonzero with one error per line naming the
   file, rule ID, and concrete fix.
3. The test-evidence checks read normalised evidence (the adapter's JUnit run
   report and per-tier collect-only test IDs), not console text. The harness
   runs `omp-guardrails report-check` and `collection-check`, and
   `budget-check` at the agent's commit only, with JUnit and temporary output
   outside the worktree. Budgets select approved per-machine limits from the
   record's `profile.md` by a deterministic local machine probe, block
   overruns at the agent's commit, treat an unknown machine as report-only
   with a prompt to record a baseline, and fail a missing, invalid or stale
   report. CI never measures or reports test timing. Name overrunning tests
   with a speed-up fix; never propose a tier move because a test is slow.
4. `omp-guardrails diff-check` implements `TEST.growth.justified` and
   `TEST.duplicate-path`. It requires one inline `behaviour:` comment per new
   test function in the language's comment syntax (Python `# behaviour:`,
   JS/TS `// behaviour:`); parameter rows share it. It checks presence and
   non-placeholder text only, and rejects duplicates by normalised call-path
   and assertion fingerprints, naming both cases and the merge-or-remove fix.
   The remaining test-guidance rules follow each catalogue row's `reference`:
   `diff-check` (coverage ratchet, golden change record, identity artefacts,
   timeout increases, test growth and duplicate paths), `python-test-hygiene`
   (subprocess timeouts), `guard-run` (user-data guard, fast-tier isolation),
   `surface-lint` and `lever-selftest` (verification surface),
   `profile-check` (`PROFILE.complete`), `adapter` (source patterns for
   self-mocking, test hooks, evasion markers and property seeds) and, in T3
   only, `mutation-check` after the adapter's `mutation-report`. Affected T2
   selection uses `omp-guardrails adapter affected --base <ref>`, which reads
   the record's Ownership areas. These run from the harness; none is wired
   into the target.
5. Draft the repo-side controls the project wants without the harness: a
   check-only full runner for `GATE.local-ci-parity` with tier selectors, and
   CI wiring that runs `checks.ci` (full T1 and T2) on every push and pull
   request, either as one job named by the record's `ci.requiredContext`
   (default `gate`) or as parallel jobs that call the same runner with tier
   selectors whose union equals `checks.ci`. Parallel CI must include an
   aggregate job and status named by `ci.requiredContext`, depending on every
   tier job. It must run even after dependency failure (for GitHub Actions,
   `if: ${{ always() }}`) and succeed only if every tier's result is
   `success`; failed, cancelled, or skipped tiers must make the required
   context fail. A commit target (`checks.commit`: static checks, every T1,
   affected T2 boundary tests) serves `GATE.pre-commit`; the repo may run it
   from its own hook manager. Do not add a pre-push hook; remove an existing
   one by the approved decision. None of these files names omp, dynaflows or
   the record.
6. Prepare candidate fixes for every Step 4 finding in the temporary clone.
   Never weaken the real repository's protection to clear a finding; a scope
   exclusion covers only generated, vendored or fixture paths. Draft
   contributor commands separately from application-user guidance. Keep all
   candidates and the draft record outside the target and outside the
   records directory until Step 5 approval; identify the superseded
   checks/config to remove, including any omp-specific files the repo holds.

## Done when

- The draft record holds every applicable setting, and every applicable ID has
  a harness check or a repo control.
- No target file and no record has been written, staged, or replaced.
