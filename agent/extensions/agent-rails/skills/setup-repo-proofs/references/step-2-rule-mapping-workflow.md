# Rule contract mapping

## Objective

Translate every catalogue rule into an exact target-stack enforcement obligation with exactly one owner.

## Required actions

1. Read every entry in [the rule catalogue](../assets/guardrail-rules.json), including the language-neutral rules.
   It is the setup-time specification: treat each ID, semantic, threshold and target-derived requirement as binding, not a category.
   Once a rule is enforced, its checker and canaries carry the contract; the catalogue is never copied into the target.
   [Verification layers](verification-layers.md) groups the families and defines `kind`, `applicability` and `evidence_kind`.
2. Map every ID to its target construct, checker or configuration, exact setting, scanned paths, exclusions and the deterministic
   points that run it. Record each per-repo value at its native home (tool, test-runner, coverage or hook config, CI workflow,
   package manifest, `CODEOWNERS`); only a value with no native home goes to a `proofs.toml` section.
   Universal (`always`) rules stay applicable in every stack. A `conditional` exclusion needs Step 1 evidence that the construct
   is absent and explicit owner approval.
3. Map `STRUCT.dependency-direction` to a layer-edge allowlist, `STRUCT.folder-placement` to path ownership,
   `NAME.conventions` to path and symbol patterns and `TEST.placement` to owner paths, in the file the owning checker
   already reads (for example a structure policy or tier map); only when none exists, in `proofs.toml` `[structure]` and `[layout]`.
   Unmapped edges and files fail; unmapped boundary-test (T2) files fail; unknown changed-code ownership selects all T2.
   Diagnostics name the file and corrective edge, name or destination. Apply [structure and naming](structure-and-naming.md).
4. Map test-evidence rules per [test evidence](test-evidence.md): budgets to normalised run reports and approved per-machine limits,
   `TEST.growth.justified` and `TEST.duplicate-path` to base-ref comparisons, isolation, evasion, mocking and seed rules to
   adapter source patterns. A slow test gets a speed-up fix, never a tier move; one still over budget is a deferral for the owner.
5. Map `GATE.enforced`, `GATE.parity` and `GATE.self-check` to the deterministic points the repo uses: an installed hook,
   a CI job whose status host protection requires, or a host setting ([gate wiring](gate-wiring.md)). Agent instructions,
   contributor docs and CI jobs whose status nothing requires never enforce. Map `GIT.required-checks` to host protection
   requiring every claim's CI status and the validator status, proven by a live host query, or an approved exclusion where
   the host cannot support it.
6. For tool rules, record selector, limit, exceptions and pinned version; one rule can carry both a `selector` and a `value`.
   Use [tooling families](tooling-families.md) for the native tool and setting per language.
7. Give every rule family exactly one outcome from Step 1's rule-ID evidence:
   - `keep`: checkers a deterministic point runs already enforce every rule ID, list files from Git and apply no baseline.
   - `grow`: the repo can hold the rules (its own checker scripts, a configurable tool or its test runner), but a rule ID is absent,
     a checker applies a baseline, walks the disk, scans too narrowly or no deterministic point runs it.
     The fix is a repo change in the repo's own style, with its own tests, that adds the rules, fixes every baselined finding and
     deletes the baseline, or wires the checker in. Once it merges, the family is `keep`.
   - `add`: the repo has no capability for the family yet. Add a native tool config or a repo checker adapted from the skill's
     references (Python: [python reference](../assets/python-reference/README.md)), with positive and negative fixtures.
   Never run a duplicate checker beside an owning one, even until a `grow` change lands,
   and never offer a duplicate as the route when the owner declines a `grow` change.
8. `SECURITY.dependency-audit` is always `keep` or `grow`: any repo can hold an audit. It is `keep` only when a checker run by
   the hook and CI points meets the catalogue contract for every Step 1 lock file and manifest ([dependency audit](dependency-audit.md)).
9. A baseline of waived findings in the repo's own tools (Ruff, mypy, tsc, oxlint, golangci-lint, ShellCheck) is a `grow` fix,
   never an accepted state. mypy follows every import (`MYPY.follow_imports`): first-party errors are reported, installed third-party
   packages stay silent, each vendored third-party module gets a named `follow_imports = silent` override.
   Third-party types come from upstream: the package's own annotations, a named `follow_untyped_imports = true` override for a package
   without `py.typed`, or a published stub package such as `types-PyYAML`; use `ignore_missing_imports` only for a package with neither.
10. Every Step 1 boundary finding is a `grow` fix: delete in-repo stubs, copies and shims of third-party code and any checker
    exemption for them; scope a checker that reaches third-party code to owned files; move third-party imports and upstream workarounds
    into the owned adapter, citing the upstream issue; make tests replace the owned interface.
11. Where an applicable rule has no implementation, prepare one; keep it blocked until its checker and canaries exist.
    Record deviations as unresolved decisions, not passes. Reuse existing ownership, naming and runner policies.
    Write the per-family outcome, owner, evidence and value homes to `state.json` `map` ([run state](run-state.md)).

## Done when

- Every catalogue ID has an implementation, an evidence-backed conditional exclusion, or a blocker.
- Every family has exactly one outcome (`keep`, `grow` or `add`), and no family has two authorities.
- No applicable rule is represented solely by prose, an instruction, a tool name or an untested config.
