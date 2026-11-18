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
uv run --project <omp root>/agent/extensions/dynaflows/guardrails \
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

Per-repository values, field contracts, approvals and examples live in
[the record documentation](../../../lib/record/README.md). The harness profile template
is [`profile.md`](../../../lib/record/profile.md); generic test planning uses the separate
test-guidance template.

Each checker module's docstring lists its settings and defaults. Defaults grant
nothing: an unset allowlist approves no path, and an unset rule scope covers
every maintained root. Unconfigured repositories therefore fail with findings
instead of passing silently. Numeric defaults are the catalogue contract
values.

`omp-guardrails init --repo DIR --record DIR` drafts the configuration described in
[record setup](../../../lib/record/README.md), discovering make targets, hook managers,
CI jobs and required checks through `gh`, without writing into the repository.

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
