# Rule contract mapping

## Objective

Translate every bundled rule into an exact target-stack enforcement obligation.

## Guidance

1. Read every entry in `assets/guardrail-rules.json`, including the
   language-neutral additions. The bundled guardrail rules are the authoritative
   contract. Treat each ID, semantic, threshold, and target-derived requirement
   as the baseline, not a generic category.
2. Map every ID to its target construct, checker/configuration, exact setting,
   scanned paths, exclusions, and local/CI runner paths. Keep universal rules
   applicable in every stack. Conditional exclusions need evidence that the
   construct is absent and explicit user approval.
3. Map `STRUCT.dependency-direction` to a layer-edge allowlist and
   `STRUCT.folder-placement` to path ownership; unmapped edges/files fail. Map
   `NAME.conventions` to path/symbol patterns and `TEST.placement` to owner/tier
   paths from the repo test profile when present. Use its ownership areas and affected-T2
   selection; unmapped T2 files fail and unknown changed-code ownership selects all T2.
   Diagnostics name the file and corrective edge, name, or destination.
4. Map `TEST.budget.fast-tier-time` and `TEST.budget.per-test-time` to a checker
   consuming the normalised JUnit run report. Select limits by a deterministic local machine
   probe from the repo test profile; block timing locally in `checks.commit`, run the same
   checker report-only for CI timing. Missing, invalid or stale reports remain fatal
   everywhere. Derive limits from at least five baseline runs. First limits and raises
   require owner approval at Step 5; unknown machines report timing only and prompt to record
   a baseline.
   Map `TEST.growth.justified` to a
   base-ref diff plus a changed-behavior mapping for each new test file/case; a
   missing mapping fails with the test name and map-or-remove fix. Map
   `TEST.duplicate-path` to normalized call-path/assertion fingerprints,
   compared against existing and other new cases; duplicates fail naming both
   tests and the merge-or-remove fix.
5. Map `GATE.local-ci-parity`, `GATE.pre-commit`, and `GATE.path-check` to the
   schema version 2 `.omp/gate.json` command arrays (`checks.ci`,
   `checks.commit`, `pathCheck`), their actual local/CI/hook invocations
   (through the repo's existing hook manager, such as lefthook, husky or
   pre-commit, when one is tracked), and failure propagation. Record any
   existing pre-push hook as a practice to replace.
   Map `GIT.required-checks` to host protection requiring the `gate` status
   check on the resolved default branch; record support for auto-merge and
   rebase-merge or the evidence-backed host limitation using the catalog's
   `host_query` evidence kind.
6. For tool rules, record selector, threshold, exceptions, pinned version, and
   canaries. For bespoke rules, require executable parsers/checkers and
   positive/negative fixtures. A row with a `reference` field is implemented by
   the named `omp-guardrails` subcommand: map it to profile settings, never to
   copied or edited reference code. Elsewhere, read source-specific roots and
   allowlists from target data rather than hard-coding them.
7. Where no tool covers an applicable rule, prepare a bespoke implementation;
   keep it blocked until its checker and canaries exist. Record deviations as
   unresolved decisions, not passes. Reuse existing ownership/naming/runner
   policies rather than adding parallel authorities.

## Done when

- Every bundled ID has an implementation, evidence-backed conditional exclusion,
  or blocker.
- No applicable rule is represented solely by prose, a tool name, or an untested
  config.
