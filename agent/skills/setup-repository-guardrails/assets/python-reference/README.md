# omp-guardrails

`omp-guardrails` is the harness's Python reference for the
[rule catalogue](omp_guardrails/data/guardrail-rules.json), which ships inside
the package. It runs from this folder in the harness and inspects a
repository from outside. Repositories never install, pin, copy or mention it.
A behaviour the settings cannot express is a change to this package, made here.

## Run

The agent rails run it at the agent's commit, after the repository's own
commit command:

```text
uv run --project <omp root>/agent/skills/setup-repository-guardrails/assets/python-reference \
  omp-guardrails check --repo <worktree> --record <record dir>
```

```text
omp-guardrails <subcommand> --repo DIR --record DIR [args ...]
omp-guardrails --help
```

- `--repo` is the checkout to inspect (default: the working directory).
- `--record` is the repository's personal record,
  `<oh-mi-pi-config root>/repos/<owner>/<name>/`. Every subcommand needs it,
  and a record inside the repository is refused.
- `check` runs the static whole-repo steps over every tracked file: no
  baselines and no diff scoping. Any finding blocks the commit.
- omp-guardrails writes nothing into the repository. Output files (`--out`)
  must lie outside the checkout, and child processes write no bytecode there.
- Child processes that run repository code never see harness variables such
  as `AGENT` (`omp_guardrails/child_env.py`).

`uv.lock` locks the harness environment. `check` needs only the base
dependencies. The pytest adapter's `tiers` and `conformance` actions need the
`pytest` extra (`requirements-pytest.lock` pins it with hashes for the
container). The T3 mutation run adds the `mutation` extra.

The Vitest adapter uses the inspected repository's locked, installed Vitest;
it never downloads a runner or adds files to that repository. Set `adapter` to
`vitest` in the record. Tier `selector` values are native Vitest CLI arguments,
for example `--config config/vitest.config.ts --testNamePattern totals`;
`location` values are Vitest file filters. Prose descriptions are not selectors.
`guardrails.adapter_vitest.command` may supply an argv list instead of the
default `node <repo>/node_modules/vitest/vitest.mjs`.
`guardrails.adapter_vitest.collect_timeout_s` bounds collection (120 seconds
unless recorded otherwise). Each `tiers --out` listing uses
`<relative-file>::<full test name>` IDs and rejects empty or duplicate collections.

`adapter junit-args --tier t1 --out <external report>` emits the arguments for
a guarded Vitest run and creates its reporter shim beside that external report.
Pass those arguments to the repository's Vitest command together with its tier
selector. `guard-evidence <report> --tier t1` verifies the recorded guard evidence.
The harness setup covers setup, call and teardown, including swallowed guard
errors; collection, reports and reporter shims remain outside the checkout.
`adapter conformance` runs the canonical cases using the installed native runner;
missing dependencies fail rather than silently skipping a case.

Vitest mutation evidence uses `@stryker-mutator/core@9.4.0` JSON reports.
`adapter mutation-report --input <stryker.json> --out <external report>` converts
them to the shared schema. The input defaults to `reports/mutation/mutation.json`
and can be recorded in `guardrails.adapter_vitest.mutation_report`.
Unknown Stryker statuses and malformed locations fail; ignored, pending and
tool-error mutants remain incomplete, never counted as killed.

Each finding prints as `path: RULE: message; fix`. A finding about the record
names it `record:profile.md` or `record:gate.json`. The exit code is `0` with
no findings, `1` with findings and `2` for a usage error.

## Configure

Every per-repository value lives in the record's `profile.md` front matter:

- The test-guidance keys (tiers, layout, `public_entry_points`, budgets and
  others) feed the test-evidence, diff, environment and surface checkers.
- `guardrails.<checker>` holds checker settings, such as `guardrails.python`
  for `python-quality`, `python-scalability` and `python-fanout`, or
  `guardrails.structure` and `guardrails.naming`. A section name outside
  `GUARDRAILS_SECTIONS` in `omp_guardrails/config/profile.py` fails
  `profile-check`.
- `guardrails.check.run` picks the `check` steps (default: every step in
  `omp_guardrails/whole_repo.py` `DEFAULT_STEPS`), and `guardrails.check.exclude`
  lists generated, vendored and fixture paths whose findings are dropped.
- `guardrails.check.repo` maps a step to the repo checker files that between
  them already enforce every rule it reports, for example
  `{"python-quality": ["scripts/checks/check-python-quality.py"]}`. The repo's
  `checks.commit` runs those checkers, so `check` leaves the step out.
- `house_style.version` records the shipped
  `omp_guardrails/data/house-style.json` version. `house_style.exceptions`
  lists exceptions as `{rule, path, adr}`; an exception without an ADR is
  ignored.
- Approvals (for example `guardrails.coverage_ratchet.approvals`) are the
  owner's hand edits of the record.

Each checker module's docstring lists its settings and defaults. Defaults grant
nothing: an unset allowlist approves no path, and an unset rule scope covers
every maintained root. Unconfigured repositories therefore fail with findings
instead of passing silently. Numeric defaults are the catalogue contract
values.

`omp-guardrails init --repo DIR --record DRAFT` drafts the record for owner
confirmation into a draft directory; it refuses the live records root, and the
owner saves the reviewed draft as the live record by hand. It discovers make
targets, hook managers, CI jobs and, through `gh`, the required checks; it
writes `gate.json`, `profile.md` and proposed native-tool configs under
`proposed/` in the draft, and nothing into the
repository.

For TypeScript 7 and newer, init uses the repository's installed native compiler
(`node_modules/typescript/bin/tsc --lsp --stdio`) for its references readiness
query. TypeScript 6 and older keep `typescript-language-server --stdio`.
`PRETTIER.version` accepts the repository's own single exact Prettier pin rather
than prescribing a formatter release; the lock must match, and tracked Markdown
and YAML must invoke that pinned formatter.

## Rule coverage

The harness's package version decides which catalogue rules apply.

- Tool rules (`kind: "tool"`) and rules that name a `reference` module are
  proven once, by this package's own tests in oh-mi-pi-config
  (`tests/test_reference_*.py`). Every such rule is named by a test there.
- A rule the package does not implement stays a setup-time review item until
  the package implements it.
