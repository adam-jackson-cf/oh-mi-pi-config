# guardrails

`guardrails` is the harness's Python reference for the
[rule catalogue](guardrails/data/guardrail-rules.json), which ships inside
the package. It runs from this folder in the harness and inspects a
repository from outside. Repositories never install, pin, copy or mention it.
A behaviour the settings cannot express is a change to this package, made here.

## Run

The agent rails run it at the agent's commit, after the repository's own
commit command:

```text
uv run --project <omp root>/agent/extensions/dynaflows/engine \
  guardrails check --repo <worktree> --record <record dir>
```

```text
guardrails <subcommand> --repo DIR --record DIR [args ...]
guardrails --help
```

- `--repo` is the checkout to inspect (default: the working directory).
- `--record` is the repository's personal record,
  `<oh-mi-pi-config root>/repos/<owner>/<name>/`. Every subcommand needs it,
  and a record inside the repository is refused.
- `check` runs the static whole-repo steps over every tracked file: no
  baselines and no diff scoping. Any finding blocks the commit.
- guardrails writes nothing into the repository. Output files (`--out`)
  must lie outside the checkout, and child processes write no bytecode there.
- Child processes that run repository code never see harness variables such
  as `AGENT` (`guardrails/child_env.py`).

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

`guardrails init --repo DIR --record DIR` drafts the configuration described in
[record setup](../../../lib/record/README.md), discovering make targets, hook managers,
CI jobs and required checks through `gh`, without writing into the repository.

For TypeScript 7 and newer, init uses the repository's installed native compiler
(`node_modules/typescript/bin/tsc --lsp --stdio`) for its references readiness
query. TypeScript 6 and older keep `typescript-language-server --stdio`.
`PRETTIER.version` accepts the repository's own single exact Prettier pin rather
than prescribing a formatter release; the lock must match, and tracked Markdown
and YAML must invoke that pinned formatter.

## Language coverage

Convention packs (`guardrails/data/conventions/`) against the verification layers. The
`tooling` pack holds no source code, so only its fixed names apply.

| Layer       | python          | typescript                 | go           | shell                |
| ----------- | --------------- | -------------------------- | ------------ | -------------------- |
| Tool rules  | RUFF, MYPY      | TSC, OXLINT, PRETTIER      | GOLANGCI (1) | SHELLCHECK, SHFMT    |
| Init config | ruff, mypy (2)  | tsconfig (3), oxlintrc     | golangci     | rc, editorconfig (3) |
| Parity      | python_pins, uv | node_pins, package_manager | go           | gap (4)              |
| Adapter     | pytest          | vitest                     | gap (5)      | n/a (6)              |
| Timeouts    | subprocess      | gap (7)                    | repo (8)     | n/a (9)              |
| Fan-out     | FANOUT          | repo (8)                   | gap (10)     | n/a (9)              |
| Quality     | PYQ, PYS        | n/a (11)                   | n/a (11)     | n/a (11)             |
| Fixed names | python, tooling | typescript, tooling        | go, tooling  | tooling              |

1. gofmt and goimports are golangci-lint formatters; `standard` linters include govet.
2. Plus `.importlinter` once the record names `structure.tiers`; shell `rc` is `.shellcheckrc`.
3. An existing file is kept; init reports each `TSC.*` or `SHFMT.*` value it lacks.
4. shellcheck and shfmt have no repo-native version pin file.
5. Go has no in-process hook for the fast-tier guards and no expected-failure or retry marker,
   so eight canonical cases (five fast-tier, two expected-failure, one retry) cannot be
   expressed: an owner blocker under
   [adapter authoring](../skills/dynaflows/references/adapter-authoring.md).
   The CI image has no Go toolchain.
6. The shell pack defines no test discovery.
7. oxlint has no rule that bounds a child process.
8. A repo-owned tool rule: `GOLANGCI.RULE.forbidigo.exec-command` (Go timeouts),
   `OXLINT.LIMIT.max-dependencies` (TypeScript fan-out).
9. Shell scripts run processes by design and have no imports.
10. golangci-lint has no import-count linter.
11. PYQ and PYS judge Python class and analyzer shapes; size limits are the `LIMIT` rows.

## Rule coverage

The harness's package version decides which catalogue rules apply.

- Tool rules (`kind: "tool"`) and rules that name a `reference` module are
  proven once, by this package's own tests in oh-mi-pi-config
  (`tests/test_reference_*.py`). Every such rule is named by a test there.
- A rule the package does not implement stays a setup-time review item until
  the package implements it.
