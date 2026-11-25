# External dependencies

What agent-rails relies on outside `agent/extensions/agent-rails`. Backticked paths are repo-relative
unless they start with `~` or `<`; `tests/agent-rails-extension.test.ts` checks they exist.

## Harness and omp runtime

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Extension load | `agent/config.yml` | Absolute path: omp loads `~/.omp`, never a worktree |
| Entry file | `agent/extensions/agent-rails/package.json` | `omp.extensions` picks `index.ts` |
| Worktree base | `agent/config.yml` | `worktree.base`; guard requires worktrees under it |
| Judge role | `agent/config.yml` | `typesafe/jev-1.14.0` direct; `decide()` refuses any other |
| Judgments | `agent/extensions/agent-rails/judgments/` | Thresholds `decide()` enforces |
| omp extension API | `package.json` | `@oh-my-pi/pi-coding-agent` 19.0.1: `z`, events, `exec` |
| omp judge API | `package.json` | Public `judgment` export: `resolveJudge`, `sharedJudgmentCache` |
| omp `ask` tool | omp core | `questions` or `html` input; `in-scope` judges it |
| Session branch | omp core | `getBranch()` owner messages build the `in-scope` state |
| Turn-end event | omp core | `session_stop` block reason starts the obligation follow-up turn |
| Turn-end cleanup | omp core | `agent_end` without `willContinue` deletes a consumed handoff |
| omp settings registry | `package.json` | `lookup` pins settings; never `ttsr.disabledRules` |
| Pinned settings | omp core | Session start pins commit, github, task merge, handler timeout |
| Commit prelude off | `agent/config.yml` | `commit.enabled: false` before a session |
| Owner command | omp core | `/agentrails` onboard, configure, export, import, status |
| Interactive UI | omp core | `ctx.hasUI`: the Q&A and other prompting subcommands need it |
| omp tools | omp core | `bash`, `edit`, `ast_edit`, `write`, `eval`, `read`, `xd://`, `skill://` |
| omp eval kernel | omp core | Runs `workflows.py`; gives `agent`, `wait`, `judge` |
| Bun runtime | omp core | `Bun.YAML` reads `rails.yml`; `Bun.Glob` matches globs; `bun:sqlite` |
| Folder rules | `agent/extensions/AGENTS.md` | No `node_modules` imports; zod from omp |
| Health marker | `agent/extensions/extension-health.ts` | Expects agent-rails to register |
| Config root | Repo root | Scripts resolve by position |
| `AGENT` env var | omp runtime | Stripped from every repo command |
| `OMP_WORKTREE_DIR` | omp runtime | Overrides `worktree.base` in the guard |
| `OMP_CI_RUNNERS` | omp runtime | Overrides the `ci-runners.json` path for `ci_runner.py` |
| `OMP_REVISION` | Repo test runs | Revision stamped into adapter test evidence |
| `PI_CODING_AGENT_DIR` | omp runtime | Agent dir holding the decision store and review state |
| `TYPESAFE_API_KEY` | omp runtime | Key omp's typesafe provider sends |
| `TYPESAFE_BASE_URL` | omp runtime | Jev Inspector route; gate judgments refuse it |

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
| Git allowlist | `.gitignore` | Tracks extension and tests |

## Scripts and CI

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| CI runner | `scripts/ci_runner.py` | Merge block names its `submit`/`wait` command |
| NUC CI gate | `scripts/ci_gate.py` | `ci-gate` role; validates configs with the engine |
| Gate allowlist | `scripts/ci-gate-repos.json` | Repos `ci_gate.py` accepts |
| Device rollout | `scripts/device_rollout.py` | Runner list, omp pin; meets rollout |
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
| Rails tests | `tests/agent-rails-*.test.ts` | Entry point, guards, agent loop |
| Config tests | `tests/repo-config*.test.ts` | `lib/repo-config.ts`, `lib/git-paths.ts` |
| Config tests | `tests/test_repo_config*.py` | Engine `repo_config.py` |
| Config fixtures | `tests/fixtures/agentrails` | Valid and invalid repo configs |
| Engine tests | `ENGINE_TESTS` in `tests/test_rule_catalogue.py` | Checkers, adapters, `init` |
| Workflow tests | `tests/test_workflows*.py` | `engine/workflows.py` |
| Judgment tests | `tests/test_*judgment*.py` | `judgments/` data and version keys |
| Judged rails tests | `tests/agent-rails-judged-rails-seams.test.ts` | Stubbed judge transport |
| Tool settings | `tests/test_native_tool_settings.py` | Root ruff, mypy match engine |
| Naming tests | `tests/test_naming_scope.py` | Engine layout rules |
| Language packs | `tests/test_language_packs.py` | Engine language packs |
| Skill links | `tests/test_skill_references*.py` | `skill://` links resolve |
| Delivery policy | `tests/test_delivery_policy.py` | Bundled policy text |
| Test policy | `tests/test_test_enforcement.py` | Test enforcement docs |
| Reporter test | `tests/vitest-reporter.test.ts` | Engine vitest reporter |
| Health test | `tests/extension-health.test.ts` | Registration marker |
| CI gate test | `tests/test_ci_gate.py` | Configs from `tests/test_agentrails_fixture.py` |
| Script tests | `tests/test_ci_runner.py` | Use `guardrails.host.process` |
| Fixtures | `ENGINE_FIXTURES` in `tests/test_rule_catalogue.py` | Engine test inputs |

## Instruction files and other skills

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Config skill | `agent/skills/omp-config-optimise` | Defers merges to `git-tool` |
| Readme | `readme.md` | Layout, rails scope, config, store and review paths, CI runners |

## Machine state and repo configs

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Repo config | `<main checkout>/.agentrails/` | per `docs/config.md`; agents never write |
| NUC configs | `~/.local/share/ci-gate/configs/<owner>/<name>/` | Imported `config.json` |
| Runner roles | `~/.omp/ci-runners.json` | Commands for `ci_runner.py` |
| Review state | `~/.omp/agent/agent-rails-reviews.json` | Reviewed revision; agents never write |
| Handoff | `<worktree>/HANDOFF.md` | Excluded in `info/exclude`; never committed |
| Prove results | `<git-common-dir>/omp-prove/` | Merge needs a watched `prove()` pass |
| Structure results | `<git-common-dir>/omp-structure/` | Merge needs a watched, complete review |
| Git and gh config | `~/.gitconfig`, `~/.config/gh/config.yml` | Agent tools never write them |
| Python | `~/.local/share/uv/python` | uv-managed CPython |
| Secrets | Infisical | `TYPESAFE_API_KEY` judge key; gate status token |
| Decision store | `~/.omp/agent/agent-rails/agent-rails.db` | Decisions, reviews, rails ledger |
| Store schema | `agent/extensions/agent-rails/engine/guardrails/data/decision-store.sql` | SQL |
| NUC | `codex-omarchy-nuc` | `nuc-config` runs `ci_gate.py`; shadow review reads its store |

## Devices and GitHub

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| Required status | GitHub | `ci.requiredContext` passes on the head |
| PR state | GitHub | Head SHA and check rollup gate merges |
| Branch rules | GitHub | `guardrails init` reads required checks |
| Actions runner | GitHub | `github-actions` role runs `commands.ci` |
| Advisories | OSV, GHSA | Dependency triage records |
| TypeSafe API | Network | Answers `judge()` for the typesafe provider |

## External tools

| Dependency | Location | What it does for the extension |
| --- | --- | --- |
| `git` | PATH | Origin, aliases, status, common dir |
| `gh` | PATH | PR head, CI rollup, body, files, compare; repo description |
| jev-bridge | Loopback port 8787 | Opt-in relay behind `inspector.env`; never for gates |
| `uv` 0.13.7 | PATH | Runs `guardrails check` and `path-check` |
| `omp` | PATH | 19.0.1; `config get worktree.base`, `config get modelRoles` |
| `env` | PATH | Runs repo commands without `AGENT` |
| `python3` | PATH | Runs `ci_runner.py` |
| `ssh` | PATH | `shadow-review --machine nuc`: BatchMode, read-only store copy |
| Repo commands | Repo config | `commands.commit` before commits |
| `go`, `node`, `bun` | PATH | Engine adapter test runs |
| LSP servers | PATH | `pyright-langserver`, TS server for `init` |
| Python packages | PyPI | `ast-grep-py`, `pytest` |
