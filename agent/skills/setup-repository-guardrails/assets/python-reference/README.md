# omp-guardrails

`omp-guardrails` is the configured Python reference for the
[rule catalogue](omp_guardrails/data/guardrail-rules.json), which ships inside
the package. Repositories install it pinned and configure it with data. They
never copy, adapt or edit its code or the catalogue. A behaviour the settings
cannot express is a change to this package, made here.

## Install

Add a dev dependency pinned to a commit of the skill repository, never to a
branch, and lock it (for example with `uv.lock`):

```text
omp-guardrails @ git+https://github.com/adam-jackson-cf/oh-mi-pi-config@<commit>#subdirectory=agent/skills/setup-repository-guardrails/assets/python-reference
```

Use the `omp-guardrails[pytest]` extra when the repository runs pytest
(`requirements-pytest.lock` pins it with hashes). The T3 mutation run adds the
`mutation` extra (`requirements-mutation.lock`). The runtime code otherwise
needs only the standard library (Python 3.11 or newer). To upgrade, bump
`<commit>` in a normal pull request so the gate runs.
`omp-guardrails init` writes this pin into `[dependency-groups] dev` of
`pyproject.toml` together with the gate, hook, CI wiring and native-tool
configs; the repository's own lock command then locks it.

## Configure

Every per-repository value lives in the JSON front matter of
`.omp/test-profile.md`:

- The test-guidance keys (tiers, layout, `public_entry_points`, budgets and
  others) feed the test-evidence, diff, environment and surface checkers.
- `guardrails.<checker>` holds checker settings, such as
  `guardrails.python` for `python-quality`, `python-scalability` and
  `python-fanout`, or `guardrails.structure` and `guardrails.naming`.
- `house_style.version` records the shipped
  `omp_guardrails/data/house-style.json` version. `house_style.exceptions`
  lists exceptions as `{rule, path, adr}`; an exception without an ADR is
  ignored.

Each checker module's docstring lists its settings and defaults. Defaults grant
nothing: an unset allowlist approves no path, and an unset rule scope covers
every maintained root. Unconfigured repositories therefore fail with findings
instead of passing silently. Numeric defaults are the catalogue contract
values.

## Run

```text
omp-guardrails <subcommand> [--repo DIR] [path ...]
omp-guardrails --help
```

Each finding prints as `path: RULE: message; fix`. The exit code is `0` with
no findings, `1` with findings and `2` for a usage error.

The commit gate runs `report-check` (TEST.failure-propagation and run-report
hygiene) and `budget-check` (TEST.budget.* test-time budgets). The CI gate runs
`report-check` only: budgets apply to the local commit gate, and CI never
measures or reports test timing.

## Rule coverage

The pinned package version decides which catalogue rules apply, so a
repository keeps no catalogue copy, no validator copy and no catalogue hash.

- Tool rules (`kind: "tool"`) and rules that name a `reference` module are
  proven once, by this skill's own test suite. A repository records no ledger
  row and no canary for them.
- A rule the package does not implement needs a repository checker. Only those
  checkers, and repository-local rules marked `"local": true`, have rows in
  `.omp/rule-coverage.json` (`schema_version` 2). A repository with no such
  checker needs no ledger file.
- `rule-evidence` checks the ledger rows without running anything.
  `rule-coverage` also runs each row's canaries and hook installation check.
  `init` keeps `rule-coverage` and `canaries` out of `checks.commit`; they run
  in `checks.ci` or during setup.
