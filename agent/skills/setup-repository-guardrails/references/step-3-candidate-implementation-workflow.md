# Candidate implementation

## Objective

Prepare complete, runnable target-stack guardrails outside the target checkout.

## Guidance

1. Configure and pin the reference; author an adapter if the framework has
   none. Where the stack has a skill reference (Python:
   [`omp-guardrails`](../assets/python-reference/README.md)), never copy, adapt
   or edit its code. In a temporary clone of the target under the candidate
   root, run `omp-guardrails init` in the mode Step 0 recorded:
   `--greenfield` (no existing checks), `--adopt` (existing hand-written
   checkers that the reference replaces after a shadow run with identical or
   owner-approved violation sets) or `--extend` (guardrails exist; add only the
   missing part, such as a verification surface). `init` pins the reference as
   a dev dependency at a commit of the skill repository, with
   `#subdirectory=agent/skills/setup-repository-guardrails/assets/python-reference`;
   it writes `.omp/gate.json`, the pre-commit hook, the CI wiring (pass
   `--ci-setup` with the install command when `--runner` is `github-actions`;
   `--runner` and `--required-context` write the gate's `ci` object) and
   native-tool configs generated from the reference's data, and proves
   language-server readiness with a canary `references` query. It never
   creates empty folders. Then write every per-repo value in the front matter
   of `.omp/test-profile.md`: tiers, layout, `structure`,
   `house_style.exceptions` (each with an ADR) and `guardrails.<checker>`
   settings. A behaviour the settings cannot express is a change to the
   reference itself, never a local fork. If the target's test framework has no
   adapter, follow [adapter authoring](adapter-authoring.md). For a stack
   without a reference, reuse target tools only where their pinned version and
   configuration express the semantics; otherwise prepare a pinned tool or
   parser-based checker in the candidate tree that reads its settings from
   data, including a hashed copy of the reference's house-style data.
2. Prepare a path-check command accepting changed paths appended as separate
   arguments. Check `STRUCT.dependency-direction`, `STRUCT.folder-placement`,
   `NAME.conventions`, and `TEST.placement` using target-owned maps/patterns.
   Exit 0 when clean; otherwise exit nonzero and print one error per line naming
   the file, rule ID, and concrete fix. Include this command in the full gate
   and declare it as `pathCheck` for `GATE.path-check`. With the reference,
   this is `omp-guardrails path-check`.
3. Prepare the test-evidence checks over normalised evidence (the adapter's
   JUnit run report and per-tier collect-only test IDs), not console text.
   With the reference, configure `omp-guardrails report-check` and
   `collection-check` in `checks.commit` and `checks.ci`, and
   `omp-guardrails budget-check` in `checks.commit` only. Without one, follow
   the catalogue contracts of `TEST.budget.*`, `TEST.failure-propagation` and
   `TEST.tier-selectors`. Budgets run only on the local commit gate: select
   approved per-machine limits from the profile by a deterministic local
   machine probe, block overruns in `checks.commit`, treat an unknown machine
   as report-only with a prompt to record a baseline, and fail a missing,
   invalid or stale report. CI never measures or reports test timing. Name
   overrunning tests with a speed-up fix; never propose a tier move because a
   test is slow.
4. Prepare diff-aware checks for `TEST.growth.justified` and
   `TEST.duplicate-path`. With the reference, `omp-guardrails diff-check`
   implements both; configure the behaviour map path if the default
   `.omp/test-behavior-map.json` does not suit. Without one, compare against
   the recorded base ref; detect added files and cases, including parameter
   rows. Require a machine-readable mapping to changed source and behavior;
   reject absent/unresolved mappings, naming the test and map-or-remove fix.
   Parse call paths and assertions into normalized fingerprints; reject
   duplicates against existing/other new cases, naming both cases and the
   merge-or-remove fix. Do not rely on filename similarity alone.
   Prepare the remaining test-guidance rules from each catalogue row's
   contract; with the reference, the row's `reference` names the
   `omp-guardrails` subcommand to wire: `diff-check` (coverage ratchet, golden
   change record, identity artefacts, timeout increases, test growth and
   duplicate paths), `python-test-hygiene` (subprocess timeouts), `guard-run`
   (user-data guard, fast-tier isolation), `surface-lint` and `lever-selftest`
   (verification surface), `profile-check` (`PROFILE.complete`), `adapter`
   (source patterns for self-mocking, test hooks, evasion markers and property
   seeds) and, in T3 only, `mutation-check` after the adapter's
   `mutation-report`. The commit hook selects affected T2 with
   `omp-guardrails adapter affected --base <ref>`, which reads the profile's
   Ownership areas.
5. Draft a check-only full runner for `GATE.local-ci-parity` with tier
   selectors, and CI wiring that runs `checks.ci` (full T1 and T2) on every
   push and pull request, either as one job named by the gate's
   `ci.requiredContext` (default `gate`) or as parallel jobs that call the same
   runner with tier selectors whose union equals `checks.ci`. Parallel CI must
   include an aggregate job and status named by `ci.requiredContext`, depending
   on every tier job. It must run even after dependency
   failure (for GitHub Actions, `if: ${{ always() }}`) and succeed only if
   every tier's result is `success`; failed, cancelled, or skipped tiers must
   make the required context fail. Prepare one hook entry: pre-commit runs `checks.commit`
   (static checks, every T1, affected T2 boundary tests: seams and journeys) for
   `GATE.pre-commit`. Do not add a pre-push hook; remove an existing one by the
   approved decision. Add the entry to the repo's tracked hook manager when it
   has one; otherwise draft a native `.githooks/pre-commit`, enabled with
   `git config core.hooksPath .githooks`, that locates the repo root, parses
   `.omp/gate.json`, requires a nonempty argv, executes it without shell
   interpolation at repo root, and returns its exit status. Preserve/merge
   existing hooks by the approved decision; keep explicit fix mode separate.
   With the reference, `init` drafts this wiring and `omp-guardrails
   hook-check` proves the installation.
6. For every applicable rule the repository proves with its own checker,
   prepare compliant/violating fixtures. The checker must accept the positive
   and reject the negative with its ID. Use, as the rule needs, valid/forbidden
   edges, correct/wrong destinations and names, below/over budget reports,
   mapped/unmapped growth, distinct/duplicate fingerprints, matched/divergent
   command arrays, working/bypassed hook failures and present/missing path
   commands. Never exercise a canary by weakening the real repository's
   protection. With a skill reference, tool rules and rules with a `reference`
   field need no target fixtures: the skill's own tests prove them for every
   repository.
7. Build the ledger from `assets/coverage-ledger.example.json` with one row per
   repository checker only, and only when one exists. Use the catalogue's
   evidence kind and the
   [evidence field contract](../README.md#coverage-evidence-kinds): local/CI
   rows record both invocations and a canary; hook rows record the local
   invocation, an executable hook-install check and a behavioural canary in an
   isolated checkout. A repository with no repository checker gets no ledger
   file. Never copy the catalogue, a validator or a catalogue hash into the
   target: the pinned reference carries them. Draft contributor commands separately
   from application-user guidance. Keep all candidates outside the target until
   Step 5 approval; identify the superseded checks/config to remove.

## Done when

- Every applicable ID has a runnable candidate, and every repository checker
  has positive/negative proof inputs.
- No target file has been written, staged, or replaced.
