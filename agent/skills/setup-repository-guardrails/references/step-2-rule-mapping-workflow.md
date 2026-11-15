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
   paths from the record's `profile.md` when present. Use its ownership areas and affected-T2
   selection; unmapped T2 files fail and unknown changed-code ownership selects all T2.
   Diagnostics name the file and corrective edge, name, or destination.
4. Map `TEST.budget.fast-tier-time` and `TEST.budget.per-test-time` to a checker
   consuming the normalised JUnit run report. Select limits by a deterministic local machine
   probe from the record's `profile.md`; block timing only at the agent's commit. CI never
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
   record's `gate.json` command arrays (`checks.ci`, `checks.commit`,
   `pathCheck`). Each array names the repo's own command, such as a make
   target, never an omp path. Record the repo's actual local/CI/hook
   invocations of those commands (through its existing hook manager, such as
   lefthook, husky or pre-commit, when one is tracked) and failure
   propagation. At the agent's commit, rails run `pathCheck`, then
   `checks.commit` and then the harness checks, so a repo hook is the repo's
   own choice. Record any existing pre-push hook as a practice to replace.
6. For tool rules, record selector, limit, exceptions and pinned version; one
   tool rule may carry both a `selector` and its limit `value`. A row with a
   `reference` field names the `omp-guardrails` check step that enforces it
   from the harness, over the whole repo and configured by record settings,
   when the repo cannot hold it. Give every check step exactly one outcome
   from Step 1's
   rule-ID evidence:
   - `repo`: checkers that `checks.commit` runs enforce every rule ID the step
     reports, list files from git and apply no baseline. Map the step to the
     list of checker files in `guardrails.check.repo` (one step's rule IDs may
     be split across several checkers).
   - `grow`: the repo has the capability to hold the step's rules (its own
     checker scripts, a configurable tool, or its test runner, run by
     `checks.commit`), but a rule ID is absent, a checker applies a baseline,
     walks the disk, or is not run by `checks.commit`. The fix is a repo PR
     that adds the missing rules to the repo's checkers in the repo's own style
     and with its own tests, fixes every baselined finding and deletes the
     baseline, or wires the checker into the commit command. Once it merges,
     the step is `repo`.
   - `harness`: the repo cannot hold the step's rules without depending on omp
     or dynaflows (`profile-check` checks the record itself), or it has no
     checker capability yet (a greenfield repo).

   Never run a harness step beside a repo checker, even until a repo PR lands,
   and never offer a route where the owner declines a `grow` PR and the harness
   step runs instead. A rule that does not apply needs Step 1 evidence that its
   construct is absent and the owner's approval. `omp-guardrails`' own tests in
   oh-mi-pi-config prove the harness rules once for every repository; the
   repo's tests prove the repo's checkers. Read source-specific roots and
   allowlists from target data rather than hard-coding them.
7. The same baseline rule covers the repo's own tools for catalogue rules
   without a `reference` (Ruff, mypy and similar): a baseline of waived
   findings is a `grow` fix, never an accepted state. mypy follows every
   import (`MYPY.follow_imports`): first-party errors are reported, installed
   third-party packages stay silent, and each vendored third-party module
   found in Step 1 gets a named `follow_imports = silent` override. Where an applicable rule
   has neither a repo home nor an `omp-guardrails` subcommand, the
   implementation is a repo checker when the repo has the capability, and
   otherwise a change to `omp-guardrails` in oh-mi-pi-config with its own
   tests; keep the rule blocked until that change lands. Record deviations as
   unresolved decisions, not passes. Reuse existing ownership/naming/runner
   policies rather than adding parallel authorities.

## Done when

- Every bundled ID has an implementation, evidence-backed conditional exclusion,
  or blocker.
- No applicable rule is represented solely by prose, a tool name, or an untested
  config.
