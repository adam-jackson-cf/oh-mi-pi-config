# Verification layers

[The rule catalogue](../assets/guardrail-rules.json) (164 rules) is the setup-time specification. Each row has `id`, `kind`
(`tool`, `bespoke` or `process`), `applicability` (`always` or `conditional`), an optional `evidence_kind`
(default `local_ci`), a `contract` object and a `summary`. Python bespoke rows name their reference script in `reference`.
Contracts state semantics, never where a target stores a value; tool rows also hold the settings `propose_tool_configs.py` renders.

## Layers

| Layer | Families (rules) | Applicability | What it proves |
| --- | --- | --- | --- |
| Gate wiring | `GATE` (3), `GIT` (1) | always; GIT conditional | Deterministic enforcement point per claim, point parity, validator job, required statuses |
| Structure | `STRUCT` (5), `RENAME` (1), `PREFIX` (1) | always | Dependency direction, placement, tier readership, sibling isolation, rename residue |
| Naming | `NAME` (2) | always | Path, symbol and test-file names |
| Code integrity | `CODE` (3) | always | No added comments, placeholder bodies or product-code mocks |
| Test evidence | `TEST` (18) | always; 5 TEST conditional | Placement, tiers, budgets, growth, duplicates, isolation, evasion, mutation |
| Verification surface | `SURFACE` (3) | conditional | Feature map, journeys driven by a lever, lever isolation |
| Supply chain | `SECURITY` (1), `PARITY` (4) | always; PARITY conditional | Dependency audit of every lock; lock, package-manager and install parity |
| Python | `RUFF` (37), `MYPY` (9), `PYQ` (19), `PYS` (5), `FANOUT` (2) | conditional | Lint, types, structure quality, scalability, fan-out |
| TypeScript | `TSC` (13), `OXLINT` (10), `PRETTIER` (1) | conditional | Strict compiler, lint limits, formatter pin |
| Go | `GOLANGCI` (16) | conditional | Standard linters, limits, forbidden calls, formatting |
| Shell | `SHELLCHECK` (4), `SHFMT` (5) | conditional | Optional checks, full coverage, formatting |
| Agent skills | `SKILL` (1) | conditional | Skill metadata when the repo holds skills |

`always` rules apply in every stack. A `conditional` rule applies when its construct exists (the language, a verification surface,
property tests, golden files); excluding it needs evidence of absence and owner approval.
Language families carry portable semantics: a stack outside the four packs maps the same limits to its own tools.

## Claims and evidence kinds

The target's `proofs.toml` ([template](../assets/templates/proofs.toml)) records what the repo claims to verify and how each claim is
enforced and proven. It has `schema = 1`, a `[scope]` (`mode` `whole`, `changed` with `base_ref`, or `paths` with repo-relative `paths`),
the `[validator]` job (`status`, `file`, `ref`; required when any claim names a `ci` point) and one `[[claim]]` per control:

- `id`, and `rules`: catalogue IDs or family patterns ending in `*`. During a skill run, `--catalog` requires every catalogue ID to
  match exactly one claim or one exclusion.
- `control`: existing implementation paths.
- `enforced_by`: one or more deterministic points, each with `point` (`hook`, `ci` or `later`), the point `file`, and the `ref` text
  in that file that invokes the control; a `ci` point names its `status`. A `later` point is a later job's workflow file and
  names no status, so it never becomes a required merge status.
- `canary`: one or more `[[claim.canary]]` tables, each an argv `command` containing `{fixture}` run through the enforcement
  entrypoint, `passing` and `violating` fixture files or directories, the `expect` text the violating run prints and optional
  `timeout_seconds` (1–120) and `rule`. A tool claim needs one; a repo checker needs one per bespoke rule it enforces, naming it in
  `rule` (`--catalog` fails a bespoke rule with no such canary). Behaviour proves the control
  and its configuration; no config value is restated.
- Host claims replace `control`, `enforced_by` and `canary` with `host`: an argv `command` naming the origin repository and nonempty
  `expect` strings. `--live-host-query` runs it; for `GIT.required-checks` its output must name every claim's CI status and the
  validator status.

The catalogue's `evidence_kind` sets the point a covering claim needs: `local_ci` a `ci` point, `git_hook` a `hook` point (and the
`[hooks]` installation check, run once), `later_job` a `later` point, `host_query` a `host` query.

`[[not_applicable]]` (conditional rules only) holds `rule`, a concrete `reason`, `approved_by` as the owner stated it and `evidence`
that the construct is absent: `{absent = [globs]}` that `git ls-files` must not match (a language, golden files);
`{path, contains}` for what a file declares (for example that the package is a library); or `{command, expect}`, a read-only
probe run on every validation (for example `git grep -L` for a construct no glob names). Add `host = true` to a probe that queries the
host (for example HTTP 403 where protection is unavailable on a free private repository); it then runs only with `--live-host-query`.
An unsupported host setting is an approved exclusion, not a claim that protection exists.
`[[exception]]` holds owner-approved exceptions for repo checkers (`rule`, `path`, `reason`, `approved_by`, optional `check`);
third-party tool suppressions stay in the tool's config.
Settings with no native home live in `[structure]`, `[layout]`, `[test_budget]`, `[python_quality]`, `[surface]` and
`[identity_artefacts]`, read by the repo checker that needs them.

`scripts/validate_rule_coverage.py --structure-only` checks every field and path without running canaries or the hook check;
it is never final proof. The repo runs its shipped copy without a catalogue, so it proves the repo's claims, not catalogue coverage.

## Diagnostics

Every failing check names the file (and line where it has one), the rule ID and a concrete fix: the corrective edge, name,
destination, setting or command. A finding without a fix is a checker defect.
