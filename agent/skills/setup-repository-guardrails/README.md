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
and T2 suite), an actionable changed-path `pathCheck`, and `ciHost`. Setup also
installs the pre-commit hook; there is no pre-push hook. CI exposes a
status named `gate`: either the full runner job or an always-running aggregate
depending on every parallel tier job of that runner. The aggregate passes only
when every tier succeeded; failure, cancellation, or skipping any tier must fail
`gate`. New rules enforce dependency direction, folder ownership, naming, test
placement, fast-tier and per-test budgets, justified test growth, duplicate
call-path/assertion detection, and required host checks. Test-guidance v2 rules add
profile completeness, test-file names, tier selectors, no self-mocking, no test hooks, no
evasion, fast-tier isolation, the user-data guard, the coverage ratchet, golden change
records, identity artefacts, property seeds, mutation sensitivity and the verification
surface. Budget limits are per machine, selected by a deterministic local machine probe and
based on at least five baseline runs. The first limit and raises need owner approval.
`checks.commit` blocks locally against the approved machine
limit; CI runs the same checker report-only for timing. Missing, invalid or stale reports
are fatal everywhere; an unapproved machine reports only and prompts to record a baseline.
Supported hosts enable auto-merge and rebase-merge. Existing Step 5 approval
remains the boundary before any repository mutation.

## Reference implementation and `init`

A stack gets a skill reference once two repositories need it (the rule of two).
Python has one: the `omp-guardrails` package in `assets/python-reference/`.
Repositories configure and pin it; they never copy or edit it.

- `omp-guardrails init --greenfield|--adopt|--extend` writes `.omp/gate.json`,
  the pre-commit hook, the CI wiring, native-tool configs generated from the
  house style, and the pinned reference dependency, and checks language-server
  readiness. `adopt` replaces hand-written checkers after a shadow run;
  `extend` adds only a missing part, such as a verification surface.
- Per-repo settings live in the front matter of `.omp/test-profile.md`. Shared
  style rules live in the reference's `omp_guardrails/data/house-style.json`.
- Each catalogue row the reference implements has a `reference` field naming
  the `omp-guardrails` subcommand and module. The target still proves the rule
  through its ledger row and canaries.
- A test framework without an adapter gets one through
  [adapter authoring](references/adapter-authoring.md), accepted by a
  deterministic conformance run.

## Coverage evidence kinds

The catalog selects each rule's `evidence_kind`; ledger rows must match it. An
omitted kind means `local_ci`. All catalog IDs must have a ledger row; `blocked`
rows are plans, never enforcement evidence.

- `local_ci`: require existing `implementation_paths`, `local_runner` and
  `local_marker`, `ci_runner` and `ci_marker`, plus a `canary` with argv
  `command` using `{fixture}`, `passing` and `violating` fixture paths, and a
  `diagnostic`. Tool rules also require `configuration.path` and nonempty
  `configuration.contains` assertions.
- `git_hook`: require implementation paths, the local runner/marker, and the
  same passing/violating canary, but no fictitious CI invocation. Add
  `hook_install_check` with argv `command` and `diagnostic`. This command must
  inspect the installed hook/manager wiring in an isolated checkout, fail if
  absent or miswired, and print the diagnostic only on successful installation
  proof. Canaries must exercise actual commits/pushes there and prove failure
  propagation, without publishing to a protected remote.
- `host_query`: require `host_query.path` pointing to a recorded JSON object
  with the actual query argv `command`, integer `exit_code`, and strings
  `stdout` and `stderr`. Add nonempty `host_query.contains` output assertions
  proving the approved settings. Implemented host rules require exit 0 and
  assertions in stdout; no local/CI runner or `{fixture}` canary is claimed.
  Record the repository, resolved default branch, query time and verified head
  alongside the query so the owner can assess its scope and freshness.

A conditional rule may instead be `not_applicable` only with a concrete
`reason`, `user_approval: "approved"`, and evidence. For host rules, keep the
same `host_query` record and assertions over stdout/stderr, including failed
queries such as HTTP 403 for protection unavailable on GitHub Free private
repositories. Other conditional exclusions use `evidence_path` and
`evidence_marker`. An unsupported host setting is an approved exclusion, not a
claim that protection was installed; it does not waive existing CI checks.
Offline host-setting fixtures are still useful checker tests, but cannot replace
this live readback evidence.

`--structure-only` checks planned evidence without executing hook-install checks
or canaries. It is never final proof.

## References

- [Bundled rule contract](assets/guardrail-rules.json)
- [Python reference implementation](assets/python-reference/README.md)
- [Adapter authoring](references/adapter-authoring.md)
- [Coverage ledger shape](assets/coverage-ledger.example.json)
- [Rule coverage validator](scripts/validate_rule_coverage.py)
