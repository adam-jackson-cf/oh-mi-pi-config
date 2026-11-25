# OMP Control Plane

This repository is the owner's curated control plane for Oh My Pi (OMP): a
small, explicit source of truth for how OMP plans, routes, reviews, and
completes work. It is an orientation document for maintainers, not an
installation guide or an exhaustive reference.

## Design

The [agent-rails design directive](agent/extensions/agent-rails/docs/design-directive.md) holds the
design principles; `agent/AGENTS.md` holds the response rules and `agent/RULES.md` the session
rules. The agent-rails package owns its judged rules (`agent/extensions/agent-rails/rules/`), three
references its refusals cite
([structure and naming](agent/extensions/agent-rails/references/structure-and-naming.md),
[proofs and merge](agent/extensions/agent-rails/references/proofs-and-merge.md) and
[judgments](agent/extensions/agent-rails/references/judgments.md)), and bundles the skills it
calls: `test-guidance`, `setup-repo-proofs`, `git-tool` and `jev-actions`. `git-tool` owns Git,
PR, CI and merge authority, with its merge, CI and recovery runbook.

`agent/config.yml` routes work through named `modelRoles`, so model choice
follows the job rather than ad hoc selectors. It also defines runtime defaults
such as edit diagnostics, task LSP, checkpoints, compaction, and managed skill
locations.

Routing follows the owner's standing preferences, recorded with the process
for revisiting them in `agent/skills/omp-config-optimise/`: Claude Opus 5.5
orchestrates and plans at medium reasoning or below; Gemini 4 Carbon, through
Google's Antigravity CLI (`agy`), implements fixed-plan work; Claude Sonnet 5.5
designs; GPT-6 Luna handles simple actions. Reasoning above medium on Opus or
Astra is reserved for the `innovation-council` (Opus-led, with Astra and Gemini 4
Neon challengers) and `experiment-design` (Fable-led, Astra and Opus peers).

The `agy-cli.ts` extension registers the `agy` provider. It runs `agy -p` under the
user's own `agy` sign-in, so each device needs `agy` installed
(`curl -fsSL https://antigravity.google/cli/install.sh | bash`) and one interactive
`agy` login. agy runs its own file and shell tools, outside omp's rails.

## Ownership boundaries

OMP supplies bundled agents; this repository deliberately does not shadow them
locally. Its tracked custom-agent surface in `agent/agents/` is limited to
judgment and specialist roles: `kiss`, `designer`, `operator`,
`innovation-council`, `innovation-challenger`, `innovation-gemini-challenger`,
`experiment-design`, and `experiment-peer`.

The boundaries are intentional:

- **Configuration** selects models and runtime defaults (`agent/config.yml`).
- **Agents** provide isolated execution or judgment roles (`agent/agents/`).
- **Skills** package reusable workflows (`agent/skills/`).
- **Extensions** implement runtime mechanics (`agent/extensions/`);
  `extension-health.ts` reports registration health, not runtime correctness.

The always-loaded agent-rails extension has one governed state. Its
[rails scope](agent/extensions/agent-rails/docs/config.md#rails-scope) comes from the repository's config:
a repository with no GitHub origin gets the fixed guards only, and one with a missing or invalid
config refuses edits, commits, pushes, PRs, merges and `askFirst` commands until the owner runs
`/agentrails onboard`. The fixed guards
check the Git and GitHub writes `rails.yml` `gitCommand` lists (commit,
push, merge, rebase, reset, cherry-pick, revert, am, pull, update-ref, clean, restore, tag creation,
branch delete/force/move, stash drop, fetch into a local ref, worktree add/remove, `gh` PR, release,
repo, workflow and issue writes, `gh api` writes), including commands with Git global options, shell
separators and known or unknown command wrappers. They refuse what they cannot check: a shell
reading piped commands, a
variable command word, a script written in the same command, launchers and unknown programs naming
`git`, `gh`, `hub` or `omp`, inline interpreter code or script files that name them, eval cells
that start them, GitHub API writes through `curl`, gh aliases and extensions, retargeting options
and variables, and `git config` writes outside a short allowlist (a hooks path stays inside the
repository). Shell `-c`, `eval` and existing shell scripts are inspected recursively within parser
bounds; `cd` and `pushd` select the destination repository's policy, and pipeline segments keep
their own cwd. Unresolved directories fail closed for publishing. Quoted `echo` and search
arguments do not trigger the check.
The repo config's [`autonomy`](agent/extensions/agent-rails/docs/config.md#autonomy) sets `git push`,
`gh pr create` and `gh pr merge` to `allowed` or `ask`; `ask` waits for the owner's
`$approve autonomy:<action>:<subject>` line. A `git push` to the remote's default branch is always
refused. While a repository onboards, `gh pr merge` is refused: the owner merges the bootstrap PR.
The rails force no skill reads: guidance reaches agents as the self-contained judged rules in
`agent/extensions/agent-rails/rules/` and in refusal texts. All-session handoff, including subagents,
stays blocked until failed harness `path-check` paths pass on retry. Rails run
`guardrails path-check --repo <worktree>` from the harness.
It covers edited paths plus prior failures that `git status` reports as changed or
untracked, not every repository file.
Deleted, ignored, unchanged/restored and Git-directory paths are dropped; a failed
`git status` query fails the check. Handoff runs no suite.

In a governed repository, rails guard `gh pr merge`: CI must be green on the PR's exact
head, and `prove()`'s result file `<git-common-dir>/omp-prove/<head>.result.json`
must say `status: pass` for that head. Rails also require
`<git-common-dir>/omp-structure/<head>.json` with that head, `halt: false` and no errors; structure
findings and incomplete reviews block. Rails accept
either file only when this omp process watched the `prove()` or `structure_review()` cell write it.
Missing or invalid files block. The repo
config is the policy authority: when `ci.enabled` is true,
`ci.requiredContext` must be `SUCCESS` on the exact head as a commit status or
check run, whoever posts it, with no failing or pending entry of that name. The config's
`mergePolicies` choose the merge method and whether proof is needed per base and head prefix;
rails refuse `--admin`.

Session rails:

| Rail | Scope | Behaviour |
| --- | --- | --- |
| Turn-end obligations | Governed | Open PR (CI pending: wait), `afterMerge`, rollout; 3 asks |
| Device rollout | Governed | Only on a `$rollout` line or the `agentrails` tool's `rollout` |
| Restart review | Every repo | Extension changed: review; governed blocks Git writes until ack |
| Handoff | Every repo | `handoff` tool writes `HANDOFF.md`; next session gets it; never committed |

Judged rails run in governed repositories, except in-scope, which runs in every Git repository. They
run after the deterministic checks through `decide()`. Every
enforced judgment acts from the moment it ships; a judge failure refuses, except that an `ask` goes
through. A shadow judgment is asked and logged but never acts. Every answer, deterministic check
result and rail refusal lands in one SQLite decision store, `~/.omp/agent/agent-rails/agent-rails.db`;
`guardrails shadow-review` labels and measures what it holds.
[Rail judgments](agent/extensions/agent-rails/references/judgments.md#rail-judgments)
lists each rail, its outcomes and the owner's `$approve` line.

Repository checks follow the test tiers. The repo config declares
`commands.commit` (static checks, all T1 tests and T2 seams only for affected code) and
`commands.ci` (the full T1 and T2 suite), each naming the repo's own command. In a
governed repository, each agent commit runs harness `guardrails path-check --repo <worktree>`,
then `commands.commit`, and then `guardrails check --repo <worktree>`, also run from this
checkout, over the whole repo. Any finding blocks the commit;
there are no baselines and no diff scoping, and scope exclusions come from the config.
The CI runner named by `ci.runner` runs
`commands.ci` for every push and merge. Optional `commands.format` is the formatter `freeze()`
runs before hashing. Pulling existing commits into a branch (merge, rebase, cherry-pick, revert,
pull) runs no harness check: those commits were checked when they were made, and CI checks the
result. Rails block hook bypass flags/settings (`--no-verify`, `-n`, `core.hooksPath`) on Git
commands. Standalone executable-shell assignment, `export`/`declare`, `unset` and `env -u` of
hook-skip variables are blocked even without a Git action. Quoted messages are not assignments;
inherited external shell state is not a sandbox guarantee.

This repository's hook is `.githooks/pre-commit`, which runs exactly the config's `commands.commit`
(`bash scripts/run-ci-quality-gates.sh --tier commit`). Set up every clone with
`bash scripts/install-quality-gates.sh`: it installs the locked Bun and uv dependencies and sets
`core.hooksPath` to `.githooks`. The devcontainer's `postCreateCommand` and the GitHub Actions
workflow run it. The relative path makes every worktree that shares the clone's
`.git/config` run its own `.githooks/pre-commit`. Both gate tiers fail when `core.hooksPath` is
not `.githooks` or the hook differs from the expected text. Run the formatters with
`bash scripts/run-ci-quality-gates.sh --tier commit --fix`.

Agent-rails is an [extension package](agent/extensions/agent-rails/package.json), with runtime policy,
judged rules, plain references and bundled skills. It builds in no workflow: agents satisfy the
gates however they work.
Subagent edit paths must be absolute and cannot escape enclosing Git roots through symlinks, and
`git worktree add` targets must sit at `<worktree.base>/<repo>/<slug>` (`OMP_WORKTREE_DIR` overrides
the base). `skill://git-tool` owns Git, PR, CI and merge authority.

Native judged reply rules live inside the package and always load; the extension never changes
`ttsr.disabledRules`.

omp auto-discovers direct modules and one-level packages in `agent/extensions/`. Agent-rails is also
listed as a directory in `extensions:` so its sibling skills and rules load. Both routes resolve
the same entry file and omp de-duplicates it. A package skill keeps its bare name unless a different
skill of the same name collides.
To disable a module, add `disabledExtensions: [extension-module:<name>]`; explicitly configured
files bypass that filter, while modules expanded from configured package directories do not.

`.gitignore` is an allowlist for the canonical source kept here. Runtime and
update-managed state is ignored by default, including state that may appear
beside tracked files. Treat only the explicitly reopened paths as
repository-owned configuration; do not mistake local runtime assets for tracked
source.

## Per-repo config

A repo never depends on or mentions omp or agent-rails in tracked files. Its rails settings live
in `.agentrails/config.json` in its main checkout, kept out of git through
`<git-common-dir>/info/exclude`; linked worktrees read the main checkout's file.
[The config contract](agent/extensions/agent-rails/docs/config.md) defines its sections, their writers,
strict validation and export and import. Agent tools never write it. A repository whose config
is missing or invalid is refused with the onboarding text, naming the slug and the engine's
problems; `rails_kernel.py` refuses with the same route to `/agentrails onboard`. Rails and
`rails_kernel.py` strip the harness's `AGENT` variable from every repo command they run.

The owner manages the config with `/agentrails`, an owner slash command that agents cannot run.
Subcommands that prompt need an interactive omp session.

| Command | Does |
| --- | --- |
| `/agentrails onboard` | Starts onboarding; run again, runs the fixed Q&A and writes the config |
| `/agentrails configure` | Re-runs the Q&A on a valid config |
| `/agentrails export <file>` | Writes the valid config to a file outside every Git work tree |
| `/agentrails import <file>` | Validates an exported file against the origin, then writes it |
| `/agentrails status` | Shows the config state, its problems and the onboarding draft |

| State | Path |
| --- | --- |
| Repo config | `<main checkout>/.agentrails/config.json` |
| Onboarding draft | `<git-common-dir>/agentrails/draft.json` |
| Decision store | `~/.omp/agent/agent-rails/agent-rails.db` |
| Restart reviews | `~/.omp/agent/agent-rails-reviews.json` |
| NUC `ci-gate` config | `~/.local/share/ci-gate/configs/<owner>/<name>/config.json` on the NUC |

The NUC copy comes from `/agentrails export` on the Mac, then `ci_gate.py config-import <file>` on
the NUC; `XDG_DATA_HOME` replaces `~/.local/share` when set.

## Normal workflow

1. Start from the relevant tracked policy, configuration, skill, or extension
   rather than local runtime state.
2. Route work to the appropriate role and use a skill when it supplies the
   established workflow.
3. Gather evidence, make the smallest canonical change, and complete the cutover
   instead of preserving legacy behavior.
4. Verify the observable behavior at a scope proportionate to the change;
   registration checks alone are not behavioral proof.
5. Keep the durable source concise and leave generated runtime state untracked.

## Device rollout

Run `python3 scripts/device_rollout.py` from this Mac to fetch and report local
`~/.omp` and `~/.agents` revisions (never pull either local checkout), fast-forward
clean remote `~/.agents` checkouts, and start the NUC OMP deployment service.
It also writes each device's untracked `~/.omp/ci-runners.json` from the runner
role definitions in `scripts/device-rollout-hosts.json`.
On each reachable device it also streams the judge key (`TYPESAFE_API_KEY`, for omp's
direct `typesafe` provider behind the `judge` role) from Infisical into
`omp auth import -` over stdin, and runs an `omp find` canary whose coverage fills
the `find` column. Each device that runs omp gets an `omp-version` row: `omp --version`
must equal the `@oh-my-pi/pi-coding-agent` pin in `package.json`, else the row is
`version-mismatch` and names the device's version (in both modes). Untracked remote files are
preserved; tracked changes block pulls. Use `--check` to fetch and report only, without pulls,
service starts or key provisioning. The table exits nonzero for stale, failed,
unreachable, dirty-blocked, or behind-`main` deployments, and for `find-blocked`
devices whose canary coverage is not `complete` (including `key-failed` when key provisioning fails).
Hosts, paths, the `infisical` project and environment (shared by the judge key
and the `ci-gate` runner commands), the `judgeKey` secret and the `findCanary` query live in
`scripts/device-rollout-hosts.json`.
Local rows use the registry's `local` mapping for the lowercase short hostname
(or that hostname itself when unknown), and the local device is skipped as a remote target.

The NUC OMP runtime is managed only by `~/Projects/tools/omp-nuc-config` on
`nuc-config` and `omp-config-pull.service`; this command never runs Git in its
`~/.omp`. The only file it writes there is the untracked `ci-runners.json`. If `nuc-config`
lacks `main`, sync it (see the
[config contract](agent/extensions/agent-rails/docs/config.md#oh-mi-pi-config-example)),
then rerun the command.
The script never merges or pushes. Oh-mi-pi-config is not deployed to mac-cf2;
only its `~/.agents` checkout is a rollout target, so judge-key provisioning and
the find canary run only on devices that run omp.

## CI runners

The repo config's `ci` section declares what must pass, not where it runs:
`"ci": {"enabled": true, "runner": "ci-gate", "requiredContext": "gate"}`. Rails and `prove()`
check only that the required context is green on the exact head, whoever posts
it. The runner is a role name. Each device's untracked `~/.omp/ci-runners.json`,
written by `scripts/device_rollout.py`, maps roles to submit, wait, status and logs
commands. Push an `agent/<slug>` branch, then submit and wait:

```sh
python3 ~/.omp/scripts/ci_runner.py submit ci-gate OWNER/REPO SHA
python3 ~/.omp/scripts/ci_runner.py wait ci-gate OWNER/REPO SHA
```

Replace `SHA` with the pushed 40-character lowercase commit SHA. `status` prints
the result; `logs` prints output (`--tail N` limits it). `wait` defaults to the
role's `timeout` from the tracked role config; `--timeout` overrides it. A role
without configured `wait`/`status` commands uses a built-in GitHub poller and
needs `--context` set to the gate's `ci.requiredContext`; its timeout exits 124.
The `github-actions` role starts on push, so `submit` only reports that. A
missing role exits 2 and names the device_rollout.py command to run. This
repository's runner facts live in the
[config contract](agent/extensions/agent-rails/docs/config.md#oh-mi-pi-config-example).

### ci-gate service

The `ci-gate` role is `scripts/ci_gate.py` on the owner's Linux NUC, running
exact-SHA gates in rootless Podman with gVisor. Only repositories in
`scripts/ci-gate-repos.json` can run, and any of them may declare runner
`ci-gate`. The NUC reads its own copy of each repository's config, which
`ci_gate.py config-import <file>` validates and installs under
`${XDG_DATA_HOME:-~/.local/share}/ci-gate/configs/<owner>/<name>/config.json`
([export and import](agent/extensions/agent-rails/docs/config.md#export-and-import)).
It posts the GitHub commit status context named by the config's `ci.requiredContext`,
recorded in the job's `job.json`. A missing or invalid config, or one that does not route
CI to `ci-gate`, gets an `error` result with a `publicationError` and no status, so rails
never sees a guessed context.

Resubmitting a running SHA does not launch another job. Successful and failed
gate results stay cached; an infrastructure `error` is retried on resubmission.
`wait` returns only after final GitHub status publication finishes. It exits 0
for success, 1 for gate failure/error or status-publication failure, and 2 when its
wait expires. Publication failures preserve the gate outcome in `result.json`,
add a `publicationError`, and print the publication error to stderr.
The runner uses two containers on the same checkout. The first has network: it runs the
devcontainer's `postCreateCommand`, then the config's `commands.audit` dependency audit when
`gates.audit` is true. The second runs `commands.ci` with `--network=none`. A failed audit is
a cached `failure` and skips `commands.ci`. Bootstrap or install failures before the audit are
infrastructure `error` results, so normal resubmission recreates the checkout and retries
them. A phase marker outside the checkout distinguishes them from a nonzero audit result.
When the image's `USER` is a numeric non-root user, both containers map the checkout owner
to it with `--userns=keep-id`; a named non-root user is an infrastructure `error`.

Logs and atomic `result.json` files remain under
`${XDG_STATE_HOME:-~/.local/state}/ci-gate/jobs/<owner>/<repo>/<sha>/`;
checkouts are removed after completion. Bare mirrors live under
`${XDG_DATA_HOME:-~/.local/share}/ci-gate/mirrors/`. Jobs default to one
global slot (`CI_GATE_SLOTS`), with a 1,200-second systemd runtime limit
(`CI_GATE_TIMEOUT`). Retained logs/results and cached images need owner-managed
retention; the service never globally prunes Podman resources.

Infisical injects each allowlisted repository's `tokenEnv` into host-side fetches
and the status publisher, never the container. It names that owner's secret:
`NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF` or `NUC_GATE_GITHUB_TOKEN_ADAM_VERSED`.
Allowlisted public and private repositories are supported, including submissions
from SSH shells without a token. Grant the NUC identity access to each secret in
the Infisical project and environment named by the `infisical` setting of
`scripts/device-rollout-hosts.json`. Each fine-grained
GitHub token needs access to that owner's opted-in repositories (or **All
repositories**) with **Contents: read** and **Commit statuses: write** permissions. Missing credentials
leave local results intact but make publication exit nonzero. Public fetches
need no token; authenticated fetches use GitHub Basic authentication through
process-local Git configuration environment variables, never command-line
arguments or saved mirror configuration.

`CI_GATE_INFISICAL_PROJECT` and `CI_GATE_INFISICAL_ENV` are required, and `submit`
fails before creating job state when either is unset. `scripts/device_rollout.py`
writes both into each device's `ci-gate` runner commands from the registry's
`infisical` setting, which the judge key also uses; rerun it after changing the
setting. `CI_GATE_REPOS`,
`CI_GATE_GITHUB_API`, and executable overrides `CI_GATE_GIT`,
`CI_GATE_PODMAN`, `CI_GATE_SYSTEMD_RUN`, `CI_GATE_INFISICAL` support
deployment and isolated tests. Runtime flags are JSON arrays:
`CI_GATE_RUNTIME_FLAGS` defaults to
`["--runtime", "/usr/local/bin/runsc", "--runtime-flag=ignore-cgroups"]`;
`CI_GATE_BUILD_RUNTIME_FLAGS` defaults to `[]` (Podman's default build runtime).
On the NUC, rootless Podman 6.1.4 passes `--no-new-keyring` to its build runtime,
which runsc rejects. Builds run only the
repository's own Containerfile from the owner's allowlisted repositories;
gate execution stays inside gVisor. Images are cached by Dockerfile-content hash.

`.github/workflows/ci-quality-gates.yml` is the documented GitHub Actions fallback:
a weekly Monday 04:23 UTC run plus manual `workflow_dispatch`, not push/PR CI and
not a required context. The commit hook check in `run-ci-quality-gates.sh` reads
only `.githooks/pre-commit` and `core.hooksPath`, so the workflow can change or go without
breaking the gate.
Dispatch manually from Actions → CI quality gates → Run workflow, or:

```sh
gh workflow run ci-quality-gates.yml --repo adam-jackson-cf/oh-mi-pi-config --ref main
```

## Python checks

Root `pyproject.toml` and `uv.lock` pin ruff, mypy, pytest and pytest-cov for every owned Python
file. The ruff and mypy settings mirror the harness catalogue,
`agent/extensions/agent-rails/engine/data/native-tools.json`; change both together.

```bash
uv sync --locked
uv run ruff check     # fix = true applies safe fixes; add --no-fix to report only
uv run ruff format --check
uv run mypy
bash scripts/run-test-tier.sh t1   # or t2
```

`scripts/run-ci-quality-gates.sh` runs `ruff check`, `ruff format --check` and `mypy` over the
whole repository in both tiers, with `--locked` so a stale `uv.lock` fails the gate.

## Test tiers

`scripts/run-test-tier.sh t1|t2` runs one tier with Bun and pytest:

- T1 tests start no process, open no network, write no real home and drive no real Git,
  worktree or container. Bun runs `tests/*.test.ts` except `*-seams.test.ts`; pytest runs
  modules marked `fast`. In-process guards from `agent/extensions/agent-rails/engine/guardrails/commands/adapters/`
  (the Bun preload and the pytest plugin) fail any T1 test that starts a process, opens a
  network connection or writes under the real home.
- T2 tests are the seams: Bun runs `tests/*-seams.test.ts`; pytest runs modules marked `seam`.
  The script fails when a pytest test lacks exactly one tier marker.

The commit tier runs all T1, then T2 for each framework that a staged path can reach. The ci
tier runs all T1 and T2 and fails when coverage of owned code falls below its floors:
`scripts/check_coverage.py` joins coverage.py's Python statements with Bun's TypeScript lines
and the Node V8 lines of owned scripts that a Bun test runs under Node, such as the anti-slop
oxlint plugin under the real linter, for the line floor and uses Python branches for the branch
floor. The
CI-tier container installs uv by hash and syncs the locked environment in its `postCreateCommand`.

## Repository map

- `agent/config.yml` — model-role routing and OMP runtime defaults.
- `agent/AGENTS.md` — response rules and device notes.
- `agent/agents/` — the repository's custom evaluation and advisory roles.
- `agent/skills/omp-config-optimise/` — model-routing preferences and the
  evidence-driven process for re-tuning `agent/config.yml`.
- `agent/extensions/agent-rails/skills/` — the skills the extension bundles.
- `agent/extensions/agent-rails/references/` — the plain references its refusal texts cite.
- `agent/extensions/agent-rails/skills/jev-actions/` — TypeSafe Jev judgment design, threshold
  tuning and calibration.
- `agent/extensions/agent-rails/judgments/` — calibrated judgments behind `decide()`; the
  opt-in Jev Inspector route is `agent/extensions/agent-rails/inspector.env`.
- `agent/extensions/agent-rails/engine/` — harness-side checkers and the rule catalogue
  (`data/guardrail-rules.json`), run outside inspected repositories.
  Its own tests prove every rule it implements.
- `agent/extensions/agent-rails/docs/` — the per-repo config contract, the engine, design
  directive, known limits and external dependencies; the extension's `README.md` is the entry point.
- `agent/extensions/agent-rails/lib/` — the guards, rails runtime, Git paths, the repo
  config resolver and the eval-kernel API for proofs and judgments (`rails_kernel.py`).
- `agent/extensions/agent-rails/rails.yml` — the tool and command mappings the rails read: edit tools,
  path fields and the Git-write and `askFirst` command patterns.
- `agent/extensions/` — tracked runtime extensions, including extension-health
  reporting.
- `agent/lsp.json` — LSP configuration.
- `scripts/check_paths.py` — repo-owned source allowlist and test-layout checks retained for repo CI.
  Path arguments are relative to the working directory; no arguments checks all tracked files.
- `scripts/ci_runner.py` — runs a CI runner role's submit, wait, status or logs
  command from `~/.omp/ci-runners.json`.
- `scripts/ci_gate.py` and `scripts/ci-gate-repos.json` — `ci-gate` job service and
  repository/token-name allowlist.
- `.devcontainer/` — pinned gate image and dependency installation.
- `tests/test_ci_gate.py` — isolated CLI, container, and GitHub-status contracts.
- `.gitignore` — canonical-source allowlist and runtime-state boundary.
