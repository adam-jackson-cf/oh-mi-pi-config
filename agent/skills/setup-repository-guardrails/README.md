# setup-repository-guardrails

## Overview

Apply the bundled guardrail rules to a selected Git repository. The skill
inventories existing practice, configures and pins the stack's reference
checkers (or prepares runnable target-stack checks where none exists), measures
full and scoped impact without target edits, and applies only approved changes.
A rule is enforced only after its implementation and proof pass.

## When to use it

- A new Git repository needs a developer guardrail baseline.
- An existing repository needs full coverage without replacing its lint, test,
  or CI practice.
- A team wants to compare whole-repo adoption with new/changed-code or
  selected-path rollout.
- User-facing application guidance and contributor guidance need distinct
  owners.

## Example prompts

- "Assess this Python repository and show the complete guardrail dry run before
  editing."
- "Set up guardrails for this TypeScript repository, initially gating new
  changes."
- "Merge equivalent checks with existing CI; show rules that cannot be
  enforced."

## Agentic repository gate

Approved setup writes `.omp/gate.json` schema version 2: `checks.commit` for
the pre-commit hook (static checks, every T1, affected T2 boundary tests: seams and journeys),
`checks.ci` for CI on every push and pull request (the full T1
and T2 suite), `checks.profile` for profile completeness, an actionable
changed-path `pathCheck`, and `ci` (`requiredContext`, normally `gate`: the
status or check run that must be green on the exact head; `runner`: a role each
machine's runner config resolves to commands), and `dynaflowsContract` (the
dynaflows contract version the repo was set up against). Setup also installs the
pre-commit hook; there is no pre-push hook. CI exposes the required context:
either the full runner job or an always-running aggregate
depending on every parallel tier job of that runner. The aggregate passes only
when every tier succeeded; failure, cancellation, or skipping any tier must fail
the required context. New rules enforce dependency direction, folder ownership, naming, test
placement, fast-tier and per-test budgets, justified test growth and duplicate
call-path/assertion detection. Test-guidance v2 rules add
profile completeness, test-file names, tier selectors, no self-mocking, no test hooks, no
evasion, fast-tier isolation, the user-data guard, the coverage ratchet, golden change
records, identity artefacts, property seeds, mutation sensitivity and the verification
surface. Budget limits are per machine, selected by a deterministic local machine probe and
based on at least five baseline runs. The first limit and raises need owner approval.
`checks.commit` blocks against the approved machine
limit. Budgets apply to the local commit gate only: CI never measures or reports test
timing. Missing, invalid or stale reports are fatal; an unapproved machine reports only
and prompts to record a baseline.
The catalogue holds no host branch-protection rule: rails check the required
context on the exact head before merge. Existing Step 5 approval remains the
boundary before any repository mutation.

## Reference implementation and `init`

A stack gets a skill reference once two repositories need it (the rule of two).
Python has one: the `omp-guardrails` package in `assets/python-reference/`.
Repositories configure and pin it; they never copy or edit it.

- `omp-guardrails init --greenfield|--adopt|--extend` writes `.omp/gate.json`,
  the pre-commit hook, the CI wiring (a GitHub workflow running `checks.ci`
  when `--runner` is `github-actions`), native-tool configs (Ruff, mypy, import-linter)
  generated from the reference's data and the repository, and the pinned
  reference dependency in `pyproject.toml`. It proves language-server readiness
  with a canary `references` query. It never replaces an existing config.
  `adopt` replaces hand-written checkers after a shadow run; `extend` adds
  only a missing part, such as a verification surface. Every mode records
  `dynaflowsContract`, read from `agent/agent-rails.yml` beside the skill or from
  `--dynaflows-contract N`; `adopt` and `extend` raise a lower value, none lowers it.
- Per-repo settings live in the front matter of `.omp/test-profile.md`. Shared
  style rules live in the reference's `omp_guardrails/data/house-style.json`.
- The rule catalogue ships inside the package
  (`omp_guardrails/data/guardrail-rules.json`), so the pinned version decides
  which rules apply. A repository keeps no catalogue copy, no validator copy,
  no provenance file and no catalogue hash; upgrading is a one-line pin bump.
- Each catalogue row the reference implements has a `reference` field naming
  the `omp-guardrails` subcommand and module. Those rows, and every
  `kind: "tool"` row, are proven once by this skill's own test suite; the
  repository records no ledger row and no canary for them.
- A row's optional `aliases` lists retired rule IDs it absorbed, so older
  evidence and guidance that cite them resolve to the current rule.
- A test framework without an adapter gets one through
  [adapter authoring](references/adapter-authoring.md), accepted by a
  deterministic conformance run.

## Coverage evidence kinds

Only repository checkers have ledger rows: code a repository writes because
the pinned package does not implement the rule. The ledger is
`.omp/rule-coverage.json` with `schema_version` 2. A repository with no such
checker needs no ledger file.

- An `always` catalogue rule that no tool or `reference` module enforces needs
  exactly one row. A `conditional` one without a row is not adopted.
- A row for a rule the package proves is an error; remove it.
- A repository-local rule (an ID outside the catalogue) declares
  `"local": true` and picks its own evidence kind.

The catalogue selects each rule's `evidence_kind`; the row must match it. An
omitted kind means `local_ci`.

- `local_ci`: require existing `implementation_paths`, `local_runner` and
  `local_marker`, `ci_runner` and `ci_marker`, plus a `canary` with argv
  `command` using `{fixture}`, existing `passing` and `violating` fixture
  paths, a `diagnostic`, and an optional `timeout_seconds`.
- `git_hook`: require implementation paths, the local runner and marker, and
  the same canary, but no fictitious CI invocation. Add `hook_install_check`
  with argv `command` and `diagnostic`. This command must inspect the installed
  hook or hook-manager wiring in an isolated checkout, fail if it is absent or
  miswired, and print the diagnostic only on successful installation proof.

`omp-guardrails rule-evidence` checks the rows without running anything.
`omp-guardrails rule-coverage` also runs each row's canary and hook
installation check. It spawns processes, so it runs in `checks.ci` or during
setup, never in `checks.commit`; `init` keeps it out of the commit gate.

A stack without a skill reference has no package to prove its rules. It proves
every applicable rule with its own checkers and canaries, using the same row
shape.

## References

- [Bundled rule contract](assets/python-reference/omp_guardrails/data/guardrail-rules.json)
- [Python reference implementation](assets/python-reference/README.md)
- [Adapter authoring](references/adapter-authoring.md)
- [Coverage ledger shape](assets/coverage-ledger.example.json)
