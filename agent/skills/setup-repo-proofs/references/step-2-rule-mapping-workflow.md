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
   base-ref diff that requires every new test function to name the behavior
   it proves; a generic or placeholder name fails with the test name and
   rename-or-remove fix. Map
   `TEST.duplicate-path` to normalized call-path/assertion fingerprints,
   compared against existing and other new cases; duplicates fail naming both
   tests and the merge-or-remove fix.
5. Map `GATE.local-ci-parity` and `GATE.pre-commit` to the record's `gate.json`
   command arrays (`checks.ci`, `checks.commit`). Each names the repo's own command,
   never an omp path. Preserve the repo's CI commands and record its actual local/CI/hook
   invocations and failure propagation. Map `GATE.path-check` to harness
   `omp-guardrails path-check --repo <worktree> --record <record dir>`, not a record command.
   Required profile `guardrails.structure`, `guardrails.naming` and `guardrails.paths` configure it.
   Rails run it after edits and before commit; at commit `checks.commit` and whole-repo
   harness checks follow. A repo hook remains the repo's choice.
   Record any existing pre-push hook as a practice to replace.
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
   Harness-owned per-edit `path-check` is outside this delegation: it always runs with the
   required profile settings, even where repo checks overlap. See
   [ADR 0002](../../../../docs/adr/0002-harness-owns-path-checks.md), the principles 1–2 exception.

   `SECURITY.dependency-audit` has no check step and never the `harness`
   outcome, even in a greenfield repo: any repo can hold an audit without the
   harness. It is `repo` when a checker that `checks.commit` and `checks.ci`
   run meets the catalogue row's contract for every lock file and manifest in
   Step 1's map. Otherwise it is `grow`: each uncovered lock file, unreadable
   lock format, unlocked manifest, staged-file or scheduled trigger, severity
   filter, waiver or off-format finding line is fixed in the repo's checker,
   and a repo with no audit gets one. The repo's exception file goes into the
   record's `ownerOnlyPaths`.

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
   found in Step 1 gets a named `follow_imports = silent` override.
   Third-party types come from upstream: the package's own annotations, with a
   named `follow_untyped_imports = true` override for a package without
   `py.typed`, or a published stub package such as `types-PyYAML`. Use
   `ignore_missing_imports` only for a package with neither. Every Step 1
   boundary finding is a `grow` fix under design directive principle 10:
   delete in-repo stubs, copies and shims of third-party code and any checker
   exemption for them; scope a checker that reaches third-party code to owned
   files; move third-party imports and upstream workarounds into the owned
   adapter; make tests replace the owned interface. Where an applicable rule
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
