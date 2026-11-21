# External dependencies

What dynaflows relies on outside `agent/extensions/dynaflows`. Backticked paths are repo-relative
unless they start with `~` or `<`; `tests/dynaflows-extension.test.ts` checks they exist.

## Harness and omp runtime

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Extension load | `agent/config.yml` | Absolute path: omp loads `~/.omp`, never a worktree |
| Entry file | `agent/extensions/dynaflows/package.json` | `omp.extensions` picks `index.ts` |
| Worktree base | `agent/config.yml` | `worktree.base`; guard requires worktrees under it |
| Judge role | `agent/config.yml` | Model behind `judge()`; thresholds calibrated to it |
| Judge fallback | `agent/config.yml` | `retry.fallbackChains.judge` model when the judge fails |
| omp extension API | `package.json` | `@oh-my-pi/pi-coding-agent` 19.0.0: `z`, events, `exec` |
| Turn-end event | omp core | `session_stop` block reason starts the obligation follow-up turn |
| Turn-end cleanup | omp core | `agent_end` without `willContinue` deletes a consumed handoff |
| omp settings registry | `package.json` | `lookup("ttsr.disabledRules")`; mode toggles rules |
| omp tools | omp core | `bash`, `edit`, `ast_edit`, `read`, `xd://`, `skill://` shapes |
| omp eval kernel | omp core | Runs `workflows.py`; gives `agent`, `wait`, `judge` |
| Bun runtime | omp core | `Bun.YAML` reads `rails.yml`; `Bun.Glob` matches globs |
| Folder rules | `agent/extensions/AGENTS.md` | No `node_modules` imports; zod from omp |
| Health marker | `agent/extensions/extension-health.ts` | Expects dynaflows to register |
| Config root | Repo root | Records and scripts resolve by position |
| `AGENT` env var | omp runtime | Stripped from every repo command |
| `OMP_WORKTREE_DIR` | omp runtime | Overrides `worktree.base` in the guard |
| `OMP_CI_RUNNERS` | omp runtime | Overrides the `ci-runners.json` path for `ci_runner.py` |
| `OMP_REVISION` | Repo test runs | Revision stamped into adapter test evidence |

## Repo config and build

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Python project | `pyproject.toml` | Editable engine; ruff, mypy, coverage scope |
| Python lock | `uv.lock` | Locks the editable engine for `uv run --locked` |
| Python version | `.python-version` | Interpreter for the engine and its tests |
| JS lock | `bun.lock` | Frozen JS deps, including omp types |
| TypeScript | `tsconfig.json` | Strict type check of extension TS |
| Bun config | `bunfig.toml` | Test root `tests/` |
| Lint config | `.oxlintrc.json` | Complexity limits, anti-slop rules |
| Anti-slop plugin | `tools/oxlint/anti-slop` | Custom oxlint rules for extension TS |
| Markdown lint | `.markdownlint-cli2.jsonc` | 160-column limit for `skills/**` |
| Prettier | `.prettierrc.json` | Engine rule YAML parsed as JSON |
| Git allowlist | `.gitignore` | Tracks extension and tests; ignores `/repos/` |

## Scripts and CI

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| CI runner | `scripts/ci_runner.py` | Merge block names its `submit`/`wait` command |
| NUC CI gate | `scripts/ci_gate.py` | `ci-gate` role; imports `lib/repo_record.py` |
| Gate allowlist | `scripts/ci-gate-repos.json` | Repos `ci_gate.py` accepts |
| Device rollout | `scripts/device_rollout.py` | Writes `ci-runners.json`; meets rollout |
| Device registry | `scripts/device-rollout-hosts.json` | Hosts, runner roles, judge key |
| Quality gates | `scripts/run-ci-quality-gates.sh` | Lint, type, test, coverage gates |
| Test tiers | `scripts/run-test-tier.sh` | Loads engine bun preload, pytest plugin |
| Gate install | `scripts/install-quality-gates.sh` | Installs locked deps and hook |
| Audit | `scripts/audit-dependencies.sh` | Audits root and engine locks |
| Coverage | `scripts/check_coverage.py` | Line and branch floors |
| Path policy | `scripts/check_paths.py` | Source allowlist, test layout |
| Commit hook | `.githooks/pre-commit` | Runs the commit tier |
| Actions | `.github/workflows/ci-quality-gates.yml` | Weekly gates; not merge status |
| CI image | `.devcontainer/Containerfile` | Installs `requirements-pytest.lock` |
| Dev container | `.devcontainer/devcontainer.json` | Builds the `ci-gate` image |

## Tests

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Rails tests | `tests/dynaflows-*.test.ts` | Entry point, guards, agent loop |
| Record tests | `tests/repo-record*.test.ts` | `lib/repo-record.ts` |
| Record tests | `tests/test_repo_record*.py` | `lib/repo_record.py` |
| Engine tests | `tests/test_reference_*.py` | Checkers, adapters, `init` |
| Workflow tests | `tests/test_workflows*.py` | `engine/workflows.py` |
| Tool settings | `tests/test_native_tool_settings.py` | Root ruff, mypy match engine |
| Naming tests | `tests/test_naming_scope.py` | Engine layout rules |
| Language packs | `tests/test_language_packs.py` | Engine language packs |
| Skill links | `tests/test_skill_references*.py` | `skill://` links resolve |
| Delivery policy | `tests/test_delivery_policy.py` | Bundled policy text |
| Test policy | `tests/test_test_enforcement.py` | Test enforcement docs |
| Reporter test | `tests/vitest-reporter.test.ts` | Engine vitest reporter |
| Health test | `tests/extension-health.test.ts` | Registration marker |
| CI gate test | `tests/test_ci_gate.py` | Copies `lib/repo_record.py` |
| Script tests | `tests/test_ci_runner.py` | Use `guardrails.host.process` |
| Fixtures | `tests/fixtures/reference` | Engine test inputs |

## Instruction files and other skills

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Judgment skill | `agent/skills/jev-actions` | `skill://jev-actions` calibration |
| `tune_threshold.py` | `agent/skills/jev-actions/scripts` | Retunes judgment thresholds |
| Config skill | `agent/skills/omp-config-optimise` | Defers merges to `git-tool` |
| Path-check ADR | `docs/adr/0002-*.md` | Harness owns path checks |
| Structure ADR | `docs/adr/0003-*.md` | Checks at PR diff scope |
| Exceptions ADR | `docs/adr/0012-*.md` | Scoped naming exceptions |
| Skills ADR | `docs/adr/0013-*.md` | Package bundles its skills |
| Modes ADR | `docs/adr/0015-*.md` | Minimal and structured modes |
| Runtime rails ADR | `docs/adr/0017-*.md` | Mode file, obligations, review, handoffs |
| Readme | `readme.md` | Layout, modes, record contract |

## Machine state and records

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Records | `~/.omp/repos/<owner>/<name>/` | `gate.json` and `profile.md` |
| Runner roles | `~/.omp/ci-runners.json` | Commands for `ci_runner.py` |
| Review state | `~/.omp/agent/dynaflows-reviews.json` | Acknowledged extension revision per repo |
| Handoff | `<worktree>/HANDOFF.md` | Excluded in `info/exclude`; never committed |
| Prove results | `<git-common-dir>/omp-prove/` | Merge needs a pass for the head |
| Structure results | `<git-common-dir>/omp-structure/` | Merge needs no halt |
| Python | `~/.local/share/uv/python` | uv-managed CPython |
| Secrets | Infisical | Judge key; gate status token |
| NUC | `codex-omarchy-nuc` | `nuc-config` deploy runs `ci_gate.py` |

## Devices and GitHub

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Required status | GitHub | `ci.requiredContext` passes on the head |
| PR state | GitHub | Head SHA and check rollup gate merges |
| Branch rules | GitHub | `guardrails init` fills `gate.json` |
| Actions runner | GitHub | `github-actions` role runs `checks.ci` |
| Advisories | OSV, GHSA | Dependency triage records |

## External tools

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| `git` | PATH | Origin, aliases, status, common dir |
| `gh` | PATH | PR head, CI rollup, comments, rules |
| `uv` 0.13.7 | PATH | Runs `guardrails check` and `path-check` |
| `omp` | PATH | `omp config get worktree.base` |
| `env` | PATH | Runs repo commands without `AGENT` |
| `python3` | PATH | Runs `ci_runner.py` |
| Repo commands | Record | `checks.commit` before commits |
| `go`, `node`, `bun` | PATH | Engine adapter test runs |
| LSP servers | PATH | `pyright-langserver`, TS server for `init` |
| Python packages | PyPI | `ast-grep-py`, `pytest`, `mutmut` |
