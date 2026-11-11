# Rule contract mapping

## Objective

Translate every bundled rule into an exact target-stack enforcement obligation.

## Guidance

1. Read every entry in the rule catalogue,
   `assets/python-reference/omp_guardrails/data/guardrail-rules.json`, including
   the language-neutral additions. The bundled guardrail rules are the authoritative
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
   probe from the repo test profile; block timing in `checks.commit` only. CI never
   measures or reports test timing, so `checks.ci` never runs the budget checker. Missing,
   invalid or stale reports are fatal. The catalogue marks both rules `git_hook` evidence.
   Derive limits from at least five baseline runs. First limits and raises
   require owner approval at Step 5; unknown machines report timing only and prompt to record
   a baseline.
   Map `TEST.growth.justified` to a
   base-ref diff that requires one inline `behaviour:` comment per new test
   function; a missing or placeholder comment fails with the test name and
   comment-or-remove fix. Map
   `TEST.duplicate-path` to normalized call-path/assertion fingerprints,
   compared against existing and other new cases; duplicates fail naming both
   tests and the merge-or-remove fix.
5. Map `GATE.local-ci-parity`, `GATE.pre-commit`, and `GATE.path-check` to the
   schema version 2 `.omp/gate.json` command arrays (`checks.ci`,
   `checks.commit`, `pathCheck`), their actual local/CI/hook invocations
   (through the repo's existing hook manager, such as lefthook, husky or
   pre-commit, when one is tracked), and failure propagation. Record any
   existing pre-push hook as a practice to replace.
6. For tool rules, record selector, limit, exceptions and pinned version; one
   tool rule may carry both a `selector` and its limit `value`. A row with a
   `reference` field is implemented by the named `omp-guardrails` subcommand:
   map it to profile settings, never to copied or edited reference code. With
   a skill reference, tool rules and `reference` rules are proven by the
   skill's own tests, so the target adds no ledger row or canary for them. For
   every other bespoke or process rule, require an executable repository
   checker and positive/negative fixtures. A retired ID listed in a row's
   `aliases` maps to that row. Elsewhere, read source-specific roots and
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
