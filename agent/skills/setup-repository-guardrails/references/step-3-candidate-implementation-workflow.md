# Candidate implementation

## Objective

Prepare complete, runnable target-stack guardrails outside the target checkout.

## Guidance

1. Reuse target tools only where their pinned version and configuration express
   the semantics. Otherwise prepare a pinned tool or parser-based checker in the
   temporary candidate tree. For Python, adapt the
   [reference implementation](../assets/python-reference/README.md), preserving
   applicable Ruff, typing, `PYQ`, `PYS`, fan-out limits, and exceptions.
   Replace source-specific roots/allowlists; preserve exact nesting/import-count
   semantics.
2. Prepare a path-check command accepting changed paths appended as separate
   arguments. Check `STRUCT.dependency-direction`, `STRUCT.folder-placement`,
   `NAME.conventions`, and `TEST.placement` using target-owned maps/patterns.
   Exit 0 when clean; otherwise exit nonzero and print one error per line naming
   the file, rule ID, and concrete fix. Include this command in the full gate
   and declare it as `pathCheck` for `GATE.path-check`.
3. Prepare a test-budget checker reading the runner's JSON report, not console
   text. Normalize to `fastTierWallClockMs` and `tests` entries with `id`,
   `tier`, and `durationMs`. Measure wall-clock independently of summed parallel
   case durations; reject missing, invalid, negative, non-finite, or stale
   measurements and missing fast-tier cases everywhere. Select approved limits by machine
   from the repo test profile; block overruns locally in `checks.commit`, report CI timing only.
   An unapproved machine reports timing only and prompts to record a baseline. First limits
   and raises require owner approval under `limit_increase_requires_owner_approval`.
   Name overrunning tests with a speed-up fix using production inputs or fewer steps;
   never propose moving a test to another tier merely because it is slow.
4. Prepare diff-aware checks for `TEST.growth.justified` and
   `TEST.duplicate-path`. Compare against the recorded base ref; detect added
   files and cases, including parameter rows. Require a machine-readable mapping
   to changed source and behavior; reject absent/unresolved mappings, naming the
   test and map-or-remove fix. Parse call paths and assertions into normalized
   fingerprints; reject duplicates against existing/other new cases, naming both
   cases and the merge-or-remove fix. Do not rely on filename similarity alone.
5. Draft a check-only full runner for `GATE.local-ci-parity` with tier
   selectors, and CI wiring that runs `checks.ci` (full T1 and T2) on every
   push and pull request, either as one job named `gate` or as parallel jobs
   that call the same runner with tier selectors whose union equals
   `checks.ci`. Parallel CI must include an aggregate job and status named
   `gate`, depending on every tier job. It must run even after dependency
   failure (for GitHub Actions, `if: ${{ always() }}`) and succeed only if
   every tier's result is `success`; failed, cancelled, or skipped tiers must
   make `gate` fail. Prepare one hook entry: pre-commit runs `checks.commit`
   (static checks, every T1, affected T2 boundary tests: seams and journeys) for
   `GATE.pre-commit`. Do not add a pre-push hook; remove an existing one by the
   approved decision. Add the entry to the repo's tracked hook manager when it
   has one; otherwise draft a native `.githooks/pre-commit`, enabled with
   `git config core.hooksPath .githooks`, that locates the repo root, parses
   `.omp/gate.json`, requires a nonempty argv, executes it without shell
   interpolation at repo root, and returns its exit status. Preserve/merge
   existing hooks by the approved decision; keep explicit fix mode separate.
   Draft host settings for `GIT.required-checks`: require `gate`, auto-merge,
   and rebase-merge where supported; prepare an offline settings fixture and a
   live readback plan.
6. For every applicable rule, including all IDs above, prepare
   compliant/violating fixtures. The intended checker must accept the positive
   and reject the negative with its ID. Use valid/forbidden edges, correct/wrong
   destinations and names, below/over budget reports, mapped/unmapped growth,
   distinct/duplicate fingerprints, matched/divergent command arrays,
   working/bypassed hook failures, present/missing path commands, and
   protected/unprotected host settings. Never exercise a canary by weakening the
   real repository's protection.
7. Build the ledger from `assets/coverage-ledger.example.json`, populating every
   catalog ID. Use the catalog's evidence kind and the
   [evidence field contract](../README.md#coverage-evidence-kinds): local/CI
   rules record both invocations and canaries; hook rules record local
   invocation, an executable hook-install check, and behavioral canaries in an
   isolated checkout; host rules record the live host-query command, exit
   status, output, and setting assertions, or an evidence-backed conditional
   exclusion with reason and owner approval. Do not invent CI execution or
   fixture invocation for host settings. Draft contributor commands separately
   from application-user guidance. Keep all candidates outside the target until
   Step 5 approval; identify the superseded checks/config to remove.

## Done when

- Every applicable ID has a runnable candidate and positive/negative proof
  inputs.
- No target file has been written, staged, or replaced.
