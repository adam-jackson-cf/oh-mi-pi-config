# Test evidence

Test rules read normalised evidence, never console text: a JUnit run report per tier and collect-only test IDs per tier.
Per-repo values live at their native home (tier selectors in the test-runner config, coverage floors in the coverage config);
ownership areas, layout and per-machine budgets, which have none, live in `proofs.toml`.

## Tiers

- Tier by what a test drives, then cost; never by duration alone. Untagged is T1.
- T1 (in-memory): no process creation or network connection in setup, call or teardown (`TEST.t1-isolation`).
  CI also denies the network at the container; a guard hook is the local mechanism.
- T2 (`boundary`): seam, integration and journey tests driving a local process, build, transport or loopback seam, mapped
  to `proofs.toml` ownership areas; the hook runs affected T2, and unknown ownership selects all T2. Local tools are T2
  however costly.
- T3, outside the hook and CI entrypoints, on a schedule, by hand or as a release gate as the repo chooses, one job per tag
  (first that fits): `live` (a service outside the machine), `exploratory` (non-deterministic), `mutation`, `load`.
  No T3 tests, no T3 job.
- `deferred`: a T1 test over its approved limit after a speed-up fix (or a T2 test over a limit the repo documents) keeps
  its tag and adds `deferred`, with reason and limit in the file; a deferred T1 runs with T2 in CI, a deferred T2 on a later
  job. The owner approves it as a `proofs.toml` `[[exception]]`. A T1/T2-by-design test only a release or on-demand job
  runs is an existing deferral, not T3.
- `TEST.tier-selectors`: strict, disjoint selectors, `deferred` included, that together select every test file; overlap
  or an unselected file fails. The runner config declares the tags; hook and CI commands hold the selection expression;
  no checker's source holds either. Reuse the repo's mechanism (markers, projects, build tags, a package map a gate reads),
  else the tags above; a speed-only name (`slow`, `fast`) is a Step 5 conflict over the name. Go: count top-level tests
  from `go test -list`; a build-tagged tier is its tagged listing minus the untagged one, run with `-run`.
- `TEST.placement`, `NAME.test-files`: new tests sit with their owner (feature or code area), one tier per file, named for
  the behaviour they check, without ticket, version or date tokens. A required folder-per-tier layout is a Step 5
  conflict; nothing moves.
- Golden file: a stored file a test loads as expected output (snapshots, `contracts.json`).

## Rules and how each is checked

| Rule | Input | Fails when |
| --- | --- | --- |
| `TEST.budget.t1-time`, `TEST.budget.per-test-time` | run report, machine limits | Local commit exceeds the approved wall or per-test limit |
| `TEST.growth.justified` | base-ref diff, static test cases | A new test function lacks a descriptive behaviour name |
| `TEST.duplicate-path` | base-ref diff, call-path and assertion fingerprints | A new case duplicates an existing or other new case |
| `TEST.failure-propagation` | run report, collection | An empty collection, skipped or timed-out required check reports success |
| `TEST.no-self-mocking` | source patterns | A test mocks, stubs, fakes or patches owned code other than an owned third-party adapter interface |
| `TEST.no-test-hooks` | source patterns | Production code reads a test flag or has symbols only tests use |
| `TEST.no-evasion` | source patterns, run report, diff | Unapproved skip, xfail, focus or flaky markers, retries to green, longer timeouts |
| `TEST.user-data-guard` | guarded run | A test tries to write a real user file; HOME and XDG dirs are temp, writes are blocked first |
| `TEST.coverage-ratchet` | base-ref diff | A new coverage exclusion or pragma, or a lowered floor without approval |
| `TEST.golden-change-record` | base-ref diff | A changed golden file has no change-record entry naming it |
| `TEST.identity-artefacts` | base-ref diff | Test-only work changes an identity-bearing artefact |
| `TEST.property-seed` | source patterns | A property test neither sets nor reports its seed |
| `TEST.mutation-sensitivity` | mutation report (T3 later job) | A mutant of changed behaviour survives or stays untested |
| `TEST.subprocess-timeouts` | test source | A test process call or wait has no positive timeout |
| `SURFACE.*` | feature map, journeys, lever | Feature docs lack the four sections, journeys skip the lever, the lever leaks processes |

## Budgets

- Limits come from at least five baseline runs per machine. A deterministic local probe (platform, CPU model, runtime version)
  selects the machine's approved `wall_limit_s` and `per_test_limit_s`; first limits and any raise need owner approval.
  Limits stay provisional until the repo's first measure run confirms them; an unmeasured limit an instruction states is
  only a provisional seed.
- Timing blocks only the local commit; CI never measures or reports test timing, so no CI point runs the budget checker.
  T2 has no blocking budget; a documented T2 limit stays where it is.
- An unknown machine reports timing only and prompts for a baseline. Missing, invalid, negative, non-finite or stale reports fail.
- A hang timeout (per test, package or invocation) or a CI job timeout shared with other checks is never a budget.
- Measure T1 wall clock apart from summed parallel durations. An overrunning test gets a speed-up fix, never a tier move;
  one still over is deferred only with owner approval, and budget checkers skip deferred tests.

## Growth and duplicates

- Compare against the recorded base ref; detect added files and cases, including parameter rows.
- A behaviour name is the Python function name without `test_` and underscores, the JS/TS test title, or the Go test name
  without `Test` split at case changes; it needs two words other than test, case, todo, tbd, placeholder or example.
  Parameter rows and subtests share one name. An uncollectable changed test file fails.
- Fingerprints normalise call paths and assertions; a duplicate fails naming both tests and the merge-or-remove fix.
  Never rely on filename similarity alone.

## Verification surface

- A product that starts a process users drive (server, UI, daemon, interactive tool) gets a lever: one command performing launch,
  doctor, drive, evidence and cleanup, with per-instance ports
  and data in gitignored or temporary directories, killing only its own process group and keeping the evidence directory.
- Doctor proves ready means usable; its failure prints the declared doctor marker and counts as blocked infrastructure, never as a test result.
- The feature map holds one document per feature with exactly the H2 sections `Sub-features`, `How to get to it (user POV)`,
  `Driving it with <lever>` and `Gotchas`, linking the outcome tests or journeys that prove it; stale links fail.
- Journeys are deterministic T2 files matching the declared journey pattern that drive the product through the lever.
  A repo with no runnable app records an owner-approved `SURFACE.*` exclusion in `proofs.toml` and proves outcomes with T1 tests instead.

## Adapter interface

A test framework without an adapter gets one. An adapter does only these six things; per-repo values come from native configs and `proofs.toml`.

1. **Run report.** Emit a JUnit XML report for every tier run.
2. **Tier listing.** List collected test IDs per tier without running them, using the test runner's tier selectors. Collect static
   test cases from source: each case's ID, a fingerprint of its normalised calls and assertions, and its behaviour name.
3. **T1 guard.** Block process creation and network connections across T1 setup, call and teardown, including swallowed errors.
4. **Source patterns.** Supply ast-grep (or equivalent) rules for the framework's mock, patch, skip, focus, retry and seed idioms.
   Owned code means the packages the package manifest declares. Also report production symbols only tests use.
5. **Mutation tool.** Name and pin the framework's mutation tool (for Vitest, Stryker JSON reports) and convert its results to a shared
   report (mutant, file, line span, status). Unknown statuses and malformed locations fail; ignored, pending and tool-error mutants
   stay incomplete, never killed.
6. **Normalisation.** Convert the framework's JUnit dialect (durations, skip reasons, retry markers, empty-collection exit codes)
   into the shared schema inside the adapter, never in a shared checker. Durations feed only the local budget checker; CI points
   consume the same reports for failure propagation and never for timing. Tier listings use stable IDs (for Vitest
   `<relative-file>::<full test name>`), bound collection time, and reject empty or duplicate collections.

## Conformance

[The conformance cases](../assets/conformance-cases.json) name an input situation, the evidence layer and the verdict every adapter must produce.

1. Write every case as a fixture test in the target framework with its expected verdict, beside the adapter's own tests.
2. Freeze the fixtures before writing the adapter: the adapter changes, the fixtures do not.
3. Run the checkers over the adapter's evidence for each fixture. Accept the adapter only when every case produces its verdict.
   A case the framework cannot express is a blocker for the owner, never a silent skip. Shared checkers' own tests use synthetic
   JUnit fixtures so they never depend on a framework.

Keep framework choices only one repo needs in that repo's own config; generalise adapter code only when a second repo uses the same
framework and every conformance case still passes. Evidence and reports go to a temporary directory outside the worktree.
