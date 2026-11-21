# OMP Control Plane

This repository is the owner's curated control plane for Oh My Pi (OMP): a
small, explicit source of truth for how OMP plans, routes, reviews, and
completes work. It is an orientation document for maintainers, not an
installation guide or an exhaustive reference.

## Design

The [dynaflows design directive](agent/extensions/dynaflows/AGENTS.md#design-directive) holds the
design principles; `agent/AGENTS.md` holds the response rules and `agent/RULES.md` the session
rules. The dynaflows package owns
[delivery principles](agent/extensions/dynaflows/skills/structured-workflow/references/delivery-principles.md)
and [structure and naming](agent/extensions/dynaflows/skills/structured-workflow/references/structure-and-naming.md),
and bundles the skills it calls: `test-guidance`, `setup-repo-proofs`, `git-tool` and
`codify-omp-workflow-corrections`. `git-tool` serves both dynaflows modes; its merge, CI and recovery
runbook applies in structured mode only. `jev-actions` is a standalone skill in `agent/skills/`.
Git history retains the retired delivery-guidance copies and standalone workflow entry. The
[workflow page](agent/extensions/dynaflows/docs/workflow.html) is an explanatory view of the
canonical skill contracts.

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

The always-loaded dynaflows extension runs each repository in minimal or structured mode
([ADR 0015](docs/adr/0015-dynaflows-minimal-and-structured-modes.md)). The tracked
[`modes.json`](agent/extensions/dynaflows/modes.json) names the structured repositories; an unlisted
one is minimal ([ADR 0017](docs/adr/0017-dynaflows-runtime-rails.md)). Its guards in both modes
require `skill://git-tool` before the Git and GitHub writes `rails.yml` `gitCommand` lists (commit,
push, merge, rebase, reset, cherry-pick, revert, am, pull, update-ref, clean, restore, tag creation,
branch delete/force/move, stash drop, fetch into a local ref, worktree add/remove, `gh` PR, release,
repo, workflow and issue writes, `gh api` writes), including commands with Git global options, shell
separators and known or unknown command wrappers. They refuse what they cannot check
([ADR 0018](docs/adr/0018-rails-trust-only-what-they-saw.md)): a shell reading piped commands, a
variable command word, a script written in the same command, launchers and unknown programs naming
`git`, `gh`, `hub` or `omp`, inline interpreter code or script files that name them, eval cells
that start them, GitHub API writes through `curl`, gh aliases and extensions, retargeting options
and variables, and `git config` writes outside a short allowlist (a hooks path stays inside the
repository). Shell `-c`, `eval` and existing shell scripts are inspected recursively within parser
bounds; `cd` and `pushd` select the destination repository's policy, and pipeline segments keep
their own cwd. Unresolved directories fail closed for publishing. Quoted `echo` and search
arguments do not trigger the check.
Record `gate.json` policies with `agentMerge: "disabled"` require owner
approval for `git push`, `gh pr create` and `gh pr merge`; invalid policies fail
closed for those actions. The record is harness config that git-tool may read for
`agentMerge` and `ci` in both modes. In minimal mode (unlisted repos included), sessions get plain
omp + git-tool + the fixed Git guards: `gh pr merge` and `git push` to the remote's default
branch are refused, while pushes to other branches and `gh pr create` pass. Minimal mode has no
edit read gates, path checks or verification handoff reads.
In structured mode, Git integration commands also need
`skill://git-tool/references/merge-and-recover.md`, the default-branch push stays refused, and
for the edited path's repo targeted integrity/new-code/test reads
fire before edits. All-session handoff, including subagents, stays blocked until
verification references are read after that session's code edits in a structured-mode repo
and failed harness `path-check` paths pass on retry. Rails run
`guardrails path-check --repo <worktree> --record <record dir>` from the harness.
It covers edited paths plus prior failures that `git status` reports as changed or
untracked, not every repository file.
Deleted, ignored, unchanged/restored and Git-directory paths are dropped; a failed
`git status` query fails the check. Handoff runs no suite.

In structured mode, rails guard `gh pr merge`: CI must be green on the PR's exact
head, and `prove()`'s result file `<git-common-dir>/omp-prove/<head>.result.json`
must say `status: pass` for that head. Rails also require
`<git-common-dir>/omp-structure/<head>.json` with that head, `halt: false` and no errors; ready
structure findings and incomplete reviews block, while provisional findings do not. Rails accept
either file only when this omp process watched the `prove()` or `structure_review()` cell write it.
Missing or invalid files block even when all decisions are provisional. The repo's
record `gate.json` is the policy authority: when it declares `ci`,
`ci.requiredContext` must be `SUCCESS` on the exact head as a commit status or
check run, whoever posts it, with no failing or pending entry of that name. The record's
`mergePolicies` choose the merge method and whether proof is needed per base and head prefix;
rails refuse `--admin`.

Session rails ([ADR 0017](docs/adr/0017-dynaflows-runtime-rails.md)):

| Rail | Modes | Behaviour |
| --- | --- | --- |
| Turn-end obligations | Structured | Open PR (CI pending: wait), `afterMerge`, rollout; 3 asks |
| Device rollout | Structured | Only on a `$rollout` line or the `dynaflows` tool's `rollout` |
| Restart review | Both | Extension changed: review; structured blocks Git writes until ack |
| Handoff | Both | `handoff` tool writes `HANDOFF.md`; next session gets it; never committed |

Judged rails, structured mode only ([ADR 0017](docs/adr/0017-dynaflows-runtime-rails.md#judged-rails)),
run after the deterministic checks through `decide()`
([ADR 0020](docs/adr/0020-judgment-registry-and-direct-judge.md)). A judgment acts only once its
`thresholds.json` is `ready`; until then it runs in shadow: logged, with an `Agent rails (shadow)`
notice, and never refuses, asks for `$approve` or blocks on a judge outage. All five are provisional
today. Once `ready`:

| Event | Judgment | yes | escalate |
| --- | --- | --- | --- |
| `git commit` | `implementation-integrity` | Refused with remediation | Owner |
| `gh pr merge` | `completion-evidence` | Refused: add evidence | Owner |
| `gh pr merge` without an ADR file | `adr-needed` | Refused: add an ADR | Owner |
| Turn end with the agent's PR | `completion-evidence` | Unmet obligation | Owner |
| Restart review | `extension-impact` | Review task | Review task |
| `ask`, `askFirstCommand` command | `in-scope` | Ask first | Ask first |

`no` passes, and the review is skipped with an owner notice; an `ask` judged in scope is refused.
The owner approves an escalation with a prompt line `$approve <judgment>:<id>` that the rails print.

Repository checks follow the test tiers. The record's `gate.json` (schema version 2) declares
`checks.commit` (static checks, all T1 tests and T2 seams only for affected code) and
`checks.ci` (the full T1 and T2 suite), each naming the repo's own command. In
structured mode, each agent commit runs harness `guardrails path-check` with repo and record arguments,
then `checks.commit`, and then `guardrails check`, also run from this checkout with
`--repo <worktree> --record <record dir>`, over the whole repo. Any finding blocks the commit;
there are no baselines and no diff scoping, and scope exclusions come from the record.
The CI runner named by `ci.runner` runs
`checks.ci` for every push and merge. Optional `checks.format` is the formatter `freeze()` runs
before hashing. Pulling existing commits into a branch (merge, rebase, cherry-pick, revert,
pull) runs no harness check: those commits were checked when they were made, and CI checks the
result. Rails block hook bypass flags/settings (`--no-verify`, `-n`, `core.hooksPath`) on Git
commands. Standalone executable-shell assignment, `export`/`declare`, `unset` and `env -u` of
hook-skip variables are blocked even without a Git action, closing the earlier persistent-shell
assignment bypass. Quoted messages are not assignments; inherited external shell state is not a
sandbox guarantee. Only schema version 2 is accepted.

This repository's hook is `.githooks/pre-commit`, which runs exactly the record's `checks.commit`
(`bash scripts/run-ci-quality-gates.sh --tier commit`). Set up every clone with
`bash scripts/install-quality-gates.sh`: it installs the locked Bun and uv dependencies and sets
`core.hooksPath` to `.githooks`. The devcontainer's `postCreateCommand` and the GitHub Actions
workflow run it. The relative path makes every worktree that shares the clone's
`.git/config` run its own `.githooks/pre-commit`. Both gate tiers fail when `core.hooksPath` is
not `.githooks` or the hook differs from the expected text. Run the formatters with
`bash scripts/run-ci-quality-gates.sh --tier commit --fix`.

Dynaflows is an [extension package](agent/extensions/dynaflows/package.json), with runtime policy,
bundled workflow guidance and judged rules. Its [workflow
skill](agent/extensions/dynaflows/skills/structured-workflow/SKILL.md) is available as
`skill://structured-workflow`; its preflight step defines how the owner switches a repository
between minimal and structured mode in the main chat (for example a standalone `$dynaflows structured`
or `$dynaflows minimal` line) and how the mode, record and playbook resolve. In structured mode, rails
inject the workflow pointer, require guardrail owner approval and guard `gh pr merge` as above.
Subagent edit paths must be absolute and cannot escape enclosing Git roots through symlinks, and
`git worktree add` targets must sit at `<worktree.base>/<repo>/<slug>` (`OMP_WORKTREE_DIR` overrides
the base). `skill://git-tool` owns Git, PR, CI and merge authority.

Native judged reply rules live inside the package. At session start its extension appends the
configured `structuredRules` names to `ttsr.disabledRules` when the session repo is in minimal
mode. Existing user exclusions are preserved; structured mode leaves the setting untouched
([ADR 0015](docs/adr/0015-dynaflows-minimal-and-structured-modes.md)).

omp auto-discovers direct modules and one-level packages in `agent/extensions/`. Dynaflows is also
listed as a directory in `extensions:` so its sibling skills and rules load. Both routes resolve
the same entry file and omp de-duplicates it. A package skill keeps its bare name unless a different
skill of the same name collides. See
[ADR 0009](docs/adr/0009-dynaflows-is-an-extension-package.md).
To disable a module, add `disabledExtensions: [extension-module:<name>]`; explicitly configured
files bypass that filter, while modules expanded from configured package directories do not.

`.gitignore` is an allowlist for the canonical source kept here. Runtime and
update-managed state is ignored by default, including state that may appear
beside tracked files. Treat only the explicitly reopened paths as
repository-owned configuration; do not mistake local runtime assets for tracked
source.

## Per-repo records

A repo never depends on, contains or mentions omp or dynaflows. Its harness settings live in a
gitignored **record**, `repos/<owner>/<name>/` under this checkout's root, named by the clone's
`origin` remote. [The record contract](agent/extensions/dynaflows/record/README.md) defines its
`gate.json` and `profile.md`, strict loading and write authority; records are never committed,
because they mean nothing to anyone else who takes this config. `scripts/device_rollout.py`
copies them to devices. Dynaflows
[harness setup](agent/extensions/dynaflows/skills/structured-workflow/references/harness-setup.md)
adds record configuration to the generic `skill://setup-repo-proofs` workflow. When dynaflows
resolves to minimal mode, rails say why once at session start and at every commit, naming the slug
(or `none`) and the reason; `workflows.py` uses the same words when it refuses. Rails and
`workflows.py` strip the harness's `AGENT` variable from every repo command they run.

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
It copies the ignored `repos/` records one way from this device into the `repos/`
folder of every target device that runs omp (a registry entry with an `omp` path),
and refuses to overwrite a target record that is newer, naming it.
On each reachable device it also streams the judge key (`TYPESAFE_API_KEY`, for omp's
direct `typesafe` provider behind the `judge` role) from Infisical into
`omp auth import -` over stdin, and runs an `omp find` canary whose coverage fills
the `find` column. Each device that runs omp gets an `omp-version` row: `omp --version`
must equal the `@oh-my-pi/pi-coding-agent` pin in `package.json`, else the row is
`version-mismatch` and names the device's version (in both modes). Untracked remote files are
preserved; tracked changes block pulls. Use `--check` to fetch and report only, without pulls,
service starts, key provisioning, or record copies. The table exits nonzero for stale, failed,
unreachable, dirty-blocked, or behind-`main` deployments, and for `find-blocked`
devices whose canary coverage is not `complete` (including `key-failed` when key provisioning fails).
Hosts, paths, the `infisical` project and environment (shared by the judge key
and the `ci-gate` runner commands), the `judgeKey` secret and the `findCanary` query live in
`scripts/device-rollout-hosts.json`.
Local rows use the registry's `local` mapping for the lowercase short hostname
(or that hostname itself when unknown), and the local device is skipped as a remote target.

The NUC OMP runtime is managed only by `~/Projects/tools/omp-nuc-config` on
`nuc-config` and `omp-config-pull.service`; this command never runs Git in its
`~/.omp`. The only files it writes there are untracked: `ci-runners.json` and the
gitignored `repos/` records. If `nuc-config` lacks `main`, sync it (see the
[record docs](agent/extensions/dynaflows/record/README.md#oh-mi-pi-config-example)),
then rerun the command.
The script never merges or pushes. Oh-mi-pi-config is not deployed to mac-cf2;
only its `~/.agents` checkout is a rollout target, so judge-key provisioning and
the find canary run only on devices that run omp.

## CI runners

The record's `gate.json` declares what must pass, not where it runs:
`"ci": {"requiredContext": "gate", "runner": "ci-gate"}`. Rails and `prove()`
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
[record docs](agent/extensions/dynaflows/record/README.md#oh-mi-pi-config-example).

### ci-gate service

The `ci-gate` role is `scripts/ci_gate.py` on the owner's Linux NUC, running
exact-SHA gates in rootless Podman with gVisor. Only repositories in
`scripts/ci-gate-repos.json` can run, and any of them may declare runner
`ci-gate`. It posts the GitHub commit status context named by the repository's
record `gate.json` `ci.requiredContext`, recorded in the job's `job.json`. A repository
without a valid `ci.requiredContext` gets an `error` result with a
`publicationError` and no status, so rails never sees a guessed context.

Resubmitting a running SHA does not launch another job. Successful and failed
gate results stay cached; an infrastructure `error` is retried on resubmission.
`wait` returns only after final GitHub status publication finishes. It exits 0
for success, 1 for gate failure/error or status-publication failure, and 2 when its
wait expires. Publication failures preserve the gate outcome in `result.json`,
add a `publicationError`, and print the publication error to stderr.
The runner uses two containers on the same checkout. The first has network: it runs the
devcontainer's `postCreateCommand`, then the record's required `checks.audit` dependency audit.
The second runs `checks.ci` with `--network=none`. A failed audit is a cached `failure`
and skips `checks.ci`. Bootstrap or install failures before `checks.audit` starts are
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
which runsc rejects. The default-runtime build succeeded, followed by two full
gates under runsc with GitHub pending/success statuses. Builds run only the
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
`agent/extensions/dynaflows/engine/guardrails/data/native-tools.json`; change both together.

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
  modules marked `fast`. In-process guards from `agent/extensions/dynaflows/engine/guardrails/commands/adapters/`
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
- `agent/AGENTS.md` — engineering, verification, and response rules.
- `agent/agents/` — the repository's custom evaluation and advisory roles.
- `agent/skills/omp-config-optimise/` — model-routing preferences and the
  evidence-driven process for re-tuning `agent/config.yml`.
- `agent/extensions/dynaflows/skills/` — the workflow skill and the skills it bundles.
- `agent/skills/jev-actions/` — TypeSafe Jev judgment design, threshold tuning and calibration.
- `agent/extensions/dynaflows/judgments/` — calibrated judgments behind `decide()`; the
  opt-in Jev Inspector route is `agent/extensions/dynaflows/inspector.env`.
- `agent/extensions/dynaflows/engine/` — harness-side checkers and the rule catalogue
  (`guardrails/data/guardrail-rules.json`), run outside inspected repositories.
  Its own tests prove every rule it implements.
- `agent/extensions/dynaflows/record/` — per-repo harness configuration documentation, schema
  and examples.
- `agent/extensions/dynaflows/lib/` — the mode guards, rails runtime and the origin-to-record
  resolver for Python (`repo_record.py`) and TypeScript (`repo-record.ts`) callers.
- `agent/extensions/dynaflows/rails.yml` — required reads per mode and the judged rule names.
- `agent/extensions/dynaflows/modes.json` — the per-repo mode; unlisted repositories run minimal.
- `repos/<owner>/<name>/` — personal per-repo records; gitignored, never committed.
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
