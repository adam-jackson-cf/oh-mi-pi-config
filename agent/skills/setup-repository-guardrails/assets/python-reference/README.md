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

Each finding prints as `path: RULE: message; fix`. A finding about the record
names it `record:profile.md` or `record:gate.json`. The exit code is `0` with
no findings, `1` with findings and `2` for a usage error.

## Configure

Every per-repository value lives in the record's `profile.md` front matter:

- The test-guidance keys (tiers, layout, `public_entry_points`, budgets and
  others) feed the test-evidence, diff, environment and surface checkers.
- `guardrails.<checker>` holds checker settings, such as `guardrails.python`
  for `python-quality`, `python-scalability` and `python-fanout`, or
  `guardrails.structure` and `guardrails.naming`.
- `guardrails.check.run` picks the `check` steps (default: every step in
  `omp_guardrails/whole_repo.py` `DEFAULT_STEPS`), and `guardrails.check.exclude`
  lists generated, vendored and fixture paths whose findings are dropped.
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

`omp-guardrails init --repo DIR --record DIR` drafts the record for owner
confirmation. It discovers make targets, hook managers, CI jobs and, through
`gh`, the required checks; it writes `gate.json`, `profile.md` and proposed
native-tool configs under `proposed/` in the record, and nothing into the
repository.

## Rule coverage

The harness's package version decides which catalogue rules apply. A
repository keeps no catalogue copy, no ledger, no canary fixtures and no
validator.

- Tool rules (`kind: "tool"`) and rules that name a `reference` module are
  proven once, by this package's own tests in oh-mi-pi-config
  (`tests/test_reference_*.py`). Every such rule is named by a test there.
- A rule the package does not implement stays a setup-time review item until
  the package implements it.
