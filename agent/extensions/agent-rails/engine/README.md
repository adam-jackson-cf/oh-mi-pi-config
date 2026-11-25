# guardrails

`guardrails` is the harness's Python engine for the [rule
catalogue](guardrails/data/guardrail-rules.json), which ships inside the
package. This `engine/` folder is the `guardrails` project. It runs from this
folder in the harness and inspects a repository from outside. Repositories
never install, pin, copy or mention it. A behaviour the settings cannot
express is a change to this package, made here.

## Run

The agent rails run it at the agent's commit, after the repository's own
commit command:

```text
uv run --project <omp root>/agent/extensions/agent-rails/engine \
  guardrails check --repo <worktree>
```

```text
guardrails <subcommand> --repo DIR [args ...]
guardrails --help
```

- `--repo` is the checkout to inspect (default: the working directory).
- Settings come from the repo config, `<main checkout>/.agentrails/config.json`;
  a linked worktree reads its main checkout's file. Every subcommand except
  `init`, `config-check` and `shadow-review` needs a valid config: a missing or
  invalid one prints `config <kind>: <problems>` and exits `2`.
- `gates.rules` switches single code-check rules off; their findings are dropped.
- The `.agentrails/` and `.proofs/` folders are never scanned, like `.git`.
- `check` runs the static whole-repo steps over every tracked file: no
  baselines and no diff scoping. Any finding blocks the commit.
- guardrails writes nothing into the repository. Output files (`--out`)
  must lie outside the checkout, and child processes write no bytecode there.
- Child processes that run repository code never see harness variables such
  as `AGENT` (`guardrails/foundation/child_env.py`).

`uv.lock` locks the harness environment. `check` needs only the base
dependencies. The pytest adapter's `tiers` and `conformance` actions need the
`pytest` extra (`requirements-pytest.lock` pins it with hashes for the
container).

## Layers

The package has four layers. A module imports only its own layer or a lower
one, and two parts of one layer never import each other.

| Layer | Holds |
| --- | --- |
| `foundation` | findings, repository path matching, child-process environments, language packs |
| `context` | the repo config, Git diff ranges and path selection |
| `toolkit` | test reports, growth snapshots, affected T2 tests, Python path rules and dispatch |
| `commands` | the CLI, `check`, `init`, every checker and the test-framework adapters |

`tests/test_structure.py` checks these layers with the
`path-check` import rules.

## Adapters

An adapter belongs to a test framework, not to a tier. The config's `tests.adapters`
list holds one entry per framework the repository uses, and every tier may hold
tests from several frameworks:

```json
"adapters": [
  {"name": "pytest", "tiers": {"t2": {"selector": "-m seam", "location": "tests"}}},
  {"name": "bun_test", "tiers": {"t1": {"selector": "--test-name-pattern .", "location": "tests"}}}
]
```

Each entry gives, per tier it takes part in, the framework's native `selector`
and the `location` handed to that framework; a tier's locations are the union of
its adapters' locations, and `tiers.<tier>.command` runs the whole tier.
`guardrails adapter <name> <action>` runs one adapter; `check`'s `adapter lint`
step runs every declared adapter's `lint`. Give each adapter its own
`tiers --out` directory and pass every list to `collection-check`, one
`--tier <tier>=<list>` per adapter. `report-check` and `budget-check` need
`--dialect <name>` when the config declares more than one adapter.

pytest also collects `unittest.TestCase` classes whatever their name, so a
repository's unittest suites run unchanged; a marker selector reaches them
through a class decorator (`@pytest.mark.fast`) or a class or module
`pytestmark`, which subclasses inherit.

The Vitest adapter (`vitest`) uses the inspected repository's locked, installed Vitest;
it never downloads a runner or adds files to that repository. Selectors are native Vitest CLI arguments,
for example `--config config/vitest.config.ts --testNamePattern totals`;
`location` values are Vitest file filters. Prose descriptions are not selectors.
`checkers.adapter_vitest.command` may supply an argv list instead of the
default `node <repo>/node_modules/vitest/vitest.mjs`.
`checkers.adapter_vitest.collect_timeout_s` bounds collection (120 seconds
unless configured otherwise). Each `tiers --out` listing uses
`<relative-file>::<full test name>` IDs and rejects empty or duplicate collections.

`adapter vitest junit-args --tier t1 --out <external report>` emits the arguments for
a guarded Vitest run and creates its reporter shim beside that external report.
Pass those arguments to the repository's Vitest command together with its tier
selector. `guard-evidence <report> --tier t1` verifies the recorded guard evidence.
The harness setup covers setup, call and teardown, including swallowed guard
errors; collection, reports and reporter shims remain outside the checkout.
`adapter vitest conformance` runs the canonical cases using the installed native runner;
missing dependencies fail rather than silently skipping a case.

Vitest mutation evidence uses `@stryker-mutator/core@9.4.0` JSON reports.
`adapter vitest mutation-report --input <stryker.json> --out <external report>` converts
them to the shared schema. The input defaults to `reports/mutation/mutation.json`
and can be set in `checkers.adapter_vitest.mutation_report`.
Unknown Stryker statuses and malformed locations fail; ignored, pending and
tool-error mutants remain incomplete, never counted as killed.

The Bun adapter (`bun_test`) drives Bun's built-in `bun test` with the
repository's own `bun` (or `checkers.adapter_bun_test.command`). Selectors are
native `bun test` arguments such as `--test-name-pattern fast`; locations are
`bun test` path filters. Its preload, loaded with `--preload` from outside the
checkout, replaces `bun:test` with recording registrars:

- `tiers` runs `bun test` with every test body and hook a no-op, so only module
  top level runs, and lists `<file>::<suite> > <title>` IDs from Bun's JUnit.
- `junit-args` adds the preload and `--reporter=junit`. In-process, the preload
  blocks child processes (`node:child_process`, `Bun.spawn`, `Bun.$`), network
  (`node:net`, `fetch`, `Bun.connect`, `Bun.serve` and the rest) in T1, blocks
  writes under the real home in every tier, and redirects `HOME` and the
  temporary directory. It journals registrations, attempts, armed guards and
  violations beside the report.
- The `bun_test` dialect completes Bun's lossy JUnit from that journal:
  expected failures, retries, registered skips, suite-ordered IDs and guard
  violations. Bun leaves a test file that fails to load out of its report, so
  `report-check` requires `--exit-code` for this dialect.
- Mutation uses `@stryker-mutator/core@9.4.0` with Stryker's built-in command
  test runner (`commandRunner.command: "bun test"`, `coverageAnalysis: "off"`);
  Stryker has no Bun runner. `mutation-report` converts its JSON as for Vitest.
- Bun has no in-body skip: a runtime skip is `test.skipIf(<condition read at run
  time>)`, and the normalised reason is the test's title.

`adapter bun_test conformance` passes every canonical case.

The Go adapter (`go`) drives `go test` with the repository's Go (or
`checkers.adapter_go.command`). Selectors are native `go test` arguments such
as `-run ^TestFast` or `-tags integration`; locations are package directories
or patterns such as `./cmd/...`.

- `tiers` runs `go test -json -list` with the selector's `-run` pattern and
  build flags, applies its `-skip` pattern, and lists `<file>::<TestName>` IDs.
- `run --tier T --out FILE` runs the tier itself and writes the JUnit report:
  the engine converts `go test -json` events, so no extra tool is pinned. It
  builds the tests with `-overlay` over a patched copy of the toolchain's
  `syscall` package. In T1 the patch blocks process starts and IP sockets; in
  every tier it blocks writes under the real home. It redirects `HOME` and
  `XDG_*` and keeps the user's Go caches and settings. A blocked call panics with the rule ID
  and is journalled, so a test that recovers from it still fails the run.
- The `go` dialect requires the exit code, which the report carries. A package
  that fails outside its tests, such as a build failure, is an error case.
- Go has no expected-failure marker: a skip whose reason starts with `xfail:`
  is one, and `lint` reports it as `TEST.no-evasion`. Retry markers are the
  HashiCorp `testutil/retry` and `avast/retry-go` packages.
- Mutation uses `github.com/go-gremlins/gremlins/cmd/gremlins@v0.6.0`;
  `mutation-report --input <gremlins.json>` converts its `--output` JSON.

`adapter go conformance` passes every canonical case. It needs Go on `PATH`;
without it every case fails.

Each finding prints as `path: RULE: message; fix`. A finding about the repo
config names the section, e.g. `.agentrails/config.json#tests`. The exit code is
`0` with no findings, `1` with findings and `2` for a usage error.

## Configure

Per-repository values live in the repo config's setup sections: `commands`,
`checkers` (one object per checker), `structure`, `houseStyle` and `tests`.
Owner exceptions and approvals live in its `exceptions` and `approvals`
sections. `guardrails config-check` validates the file
([repo_config.py](guardrails/context/config/repo_config.py) is the validator); the
section template is [config-template.json](guardrails/data/config-template.json).
The [config contract](../docs/config.md) defines every section, its writer and its rules.

Each checker module's docstring lists its settings and defaults. Defaults grant
nothing: an unset allowlist approves no path, and an unset rule scope covers
every maintained root. Unconfigured repositories therefore fail with findings
instead of passing silently. Numeric defaults are the catalogue contract
values.

`guardrails init --repo DIR --out FILE` drafts the setup sections (`commands`,
`checkers`, `structure`, `houseStyle`, `tests`) into a file outside the
repository, starting from the repo's config or the section template. It
discovers make targets, hook managers, CI jobs and required checks through `gh`,
prints native tool configs the repository lacks and runs the language-server
canaries. It never writes into the repository or its config.

For TypeScript 7 and newer, init uses the repository's installed native compiler
(`node_modules/typescript/bin/tsc --lsp --stdio`) for its references readiness
query. TypeScript 6 and older keep `typescript-language-server --stdio`.
`PRETTIER.version` accepts the repository's own single exact Prettier pin rather
than prescribing a formatter release; the lock must match, and tracked Markdown
and YAML must invoke that pinned formatter.

## Language coverage

Convention packs (`guardrails/data/conventions/`) against the verification layers. The
`tooling` pack holds no source code, so only its fixed names apply. A repository declares
one adapter per framework it uses, so one stack can use several adapters.

| Layer       | python          | typescript                 | go           | shell                |
| ----------- | --------------- | -------------------------- | ------------ | -------------------- |
| Tool rules  | RUFF, MYPY      | TSC, OXLINT, PRETTIER      | GOLANGCI (1) | SHELLCHECK, SHFMT    |
| Init config | ruff, mypy (2)  | tsconfig (3), oxlintrc     | golangci     | rc, editorconfig (3) |
| Parity      | python_pins, uv | node_pins, package_manager | go           | gap (4)              |
| Adapter     | pytest (12)     | vitest, bun_test           | go (5)       | n/a (6)              |
| Mutation    | mutmut          | Stryker (13)               | gremlins     | n/a (6)              |
| Timeouts    | subprocess      | gap (7)                    | repo (8)     | n/a (9)              |
| Fan-out     | FANOUT          | repo (8)                   | gap (10)     | n/a (9)              |
| Quality     | PYQ, PYS        | n/a (11)                   | n/a (11)     | n/a (11)             |
| Fixed names | python, tooling | typescript, tooling        | go, tooling  | tooling              |

1. gofmt and goimports are golangci-lint formatters; `standard` linters include govet.
2. Plus `.importlinter` once the config names `structure.tiers`; shell `rc` is `.shellcheckrc`.
3. An existing file is kept; init reports each `TSC.*` or `SHFMT.*` value it lacks.
4. shellcheck and shfmt have no repo-native version pin file.
5. The Go guards patch the toolchain's `syscall` package through `go test -overlay`. An
   `xfail:` skip reason is Go's expected-failure marker. The CI image pins Go 1.26.10.
6. The shell pack defines no test discovery.
7. oxlint has no rule that bounds a child process.
8. A repo-owned tool rule: `GOLANGCI.RULE.forbidigo.exec-command` (Go timeouts),
   `OXLINT.LIMIT.max-dependencies` (TypeScript fan-out).
9. Shell scripts run processes by design and have no imports.
10. golangci-lint has no import-count linter.
11. PYQ and PYS judge Python class and analyzer shapes; size limits are the `LIMIT` rows.
12. pytest also runs `unittest.TestCase` suites unchanged; tier markers reach them by class
    decorator or `pytestmark`.
13. StrykerJS JSON reports for both adapters. Stryker has no Bun runner, so Bun runs through
    Stryker's built-in command runner, which reports no per-test coverage.

## Rule coverage

- Tool rules (`kind: "tool"`) and rules that name an `implementation` module are
  proven once, by this package's own tests in oh-mi-pi-config
  (`ENGINE_TESTS` in `tests/test_rule_catalogue.py`). Every such rule is named by a test there.
- A rule the package does not implement stays a setup-time review item until
  the package implements it.
