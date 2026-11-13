# setup-repository-guardrails

## Overview

Apply the bundled guardrail rules to a selected Git repository. The skill
inventories existing practice, drafts the repo's local record for the harness
checkers (or prepares runnable target-stack checks where none exists), measures
whole-repo impact without target edits, and applies only approved fixes.
A rule is enforced only after its implementation and proof pass.

## When to use it

- A new Git repository needs a developer guardrail baseline.
- An existing repository needs full coverage without replacing its lint, test,
  or CI practice.
- User-facing application guidance and contributor guidance need distinct
  owners.

## Example prompts

- "Assess this Python repository and show the complete guardrail dry run before
  editing."
- "Set up guardrails for this TypeScript repository and list every finding to
  clear first."
- "Merge equivalent checks with existing CI; show rules that cannot be
  enforced."

## Harness/repo boundary

The target repository never depends on, contains or mentions omp or dynaflows. It keeps
conforming code and the controls the project wants without the harness: build, tests, its own
linters and checks, gate targets, hook config and CI job. Harness settings live in the repo's
personal **record**, `repos/<owner>/<name>/` under the oh-mi-pi-config root, resolved from the
clone's `origin` remote. `/repos/` is gitignored, so records are never committed; the device
rollout copies them to every device. The [example record](assets/example-record/README.md)
defines the layout.

## Record gate

The record's `gate.json` (schema version 2) names the repo's own commands: `checks.commit` (static
checks, every T1, affected T2 boundary tests), `checks.ci` (the full T1 and T2 suite),
optional `checks.profile`, an actionable changed-path `pathCheck`, and `ci` (`requiredContext`,
normally `gate`: the status or check run that must be green on the exact head; `runner`: a role
each machine's runner config resolves to commands). It also holds `agentMerge` and
`dynaflows.enabled`. `pathCheck` and `checks.commit` are required, and setup always drafts
both; a record without either, or with a field outside the schema, fails to load with an error
naming the field.

At each agent commit, rails run the record's `pathCheck`, then its `checks.commit` in
the worktree, then the harness
checks: `omp-guardrails check --repo <worktree> --record <record dir>`, run with
`uv run --project` on this skill's `assets/python-reference`. The check covers the whole repo
and blocks on any finding. There are no baselines and no diff scoping; generated, vendored and
fixture exclusions come from the record. The repo's required CI context stays its own `gate`.

New rules enforce dependency direction, folder ownership, naming, test placement, fast-tier and
per-test budgets, justified test growth and duplicate call-path/assertion detection.
Test-guidance v2 rules add profile completeness, test-file names, tier selectors, no
self-mocking, no test hooks, no evasion, fast-tier isolation, the user-data guard, the coverage
ratchet, golden change records, identity artefacts, property seeds, mutation sensitivity and the
verification surface. Budget limits are per machine, selected by a deterministic local machine
probe and based on at least five baseline runs. Budgets apply to the local commit gate only: CI
never measures or reports test timing.

Approvals and exceptions live only in the record's `profile.md`. Only the owner writes the record,
by hand: rails refuse every agent write under the records root. The Step 5 approval remains the
boundary before any repository fix.

## Reference implementation and `init`

A stack gets a skill reference once two repositories need it (the rule of two). Python has one:
the `omp-guardrails` package in `assets/python-reference/`. The harness runs it from this
checkout; repositories never install, pin or copy it, and it writes nothing into the repo tree.

- `omp-guardrails init --repo <clone> --record <draft dir>` drafts the record for owner
  confirmation into a draft directory outside the live `repos/` root (it refuses the live record;
  to extend one, copy it to the draft first). It discovers make targets, hook managers, CI job
  names and, through `gh`, the default branch's required checks. It writes `gate.json` and
  `profile.md` into the draft and ends by naming where the owner saves it by hand, and writes
  proposed native-tool configs (Ruff, mypy, import-linter) into the draft's `proposed/` folder
  for the owner to apply to the repo as a fix. It proves language-server readiness with a canary
  `references` query. It never overwrites a recorded value, never writes an approval and never
  turns dynaflows on.
- Per-repo settings live in the front matter of the record's `profile.md`. Shared style rules
  live in the reference's `omp_guardrails/data/house-style.json`.
- The rule catalogue ships inside the package (`omp_guardrails/data/guardrail-rules.json`).
  Each catalogue row the reference implements has a `reference` field naming the
  `omp-guardrails` subcommand and module. The package's own tests in oh-mi-pi-config prove
  every rule the package implements, once for every repository.
- A test framework without an adapter gets one through
  [adapter authoring](references/adapter-authoring.md), accepted by a deterministic conformance
  run.

## References

- [Bundled rule contract](assets/python-reference/omp_guardrails/data/guardrail-rules.json)
- [Python reference implementation](assets/python-reference/README.md)
- [Example record](assets/example-record/README.md)
- [Adapter authoring](references/adapter-authoring.md)
