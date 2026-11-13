# OMP Control Plane

This repository is the owner's curated control plane for Oh My Pi (OMP): a
small, explicit source of truth for how OMP plans, routes, reviews, and
completes work. It is an orientation document for maintainers, not an
installation guide or an exhaustive reference.

## Design

The configuration favors evidence before assertion, verification proportional to
risk, and clean cutovers over compatibility layers. Changes should solve the
underlying problem, preserve canonical wording where it matters, migrate callers
when a design changes, and remove obsolete paths rather than carry them forward.
`agent/AGENTS.md` holds the durable operating policy.

`agent/config.yml` routes work through named `modelRoles`, so model choice
follows the job rather than ad hoc selectors. It also defines runtime defaults
such as edit diagnostics, task LSP, checkpoints, compaction, and managed skill
locations.

Routing follows the owner's standing preferences, recorded with the process
for revisiting them in `agent/skills/omp-config-optimise/`: Claude Opus 5.5
orchestrates and plans at medium reasoning or below; GPT-6.1 Sol implements
fixed-plan work; Claude Sonnet 5.5 designs; GPT-6 Luna handles
simple actions. Reasoning above medium on Opus or Astra is reserved
for the `innovation-council` (Opus-led, Astra challenger) and
`experiment-design` (Fable-led, Astra and Opus peers).

## Ownership boundaries

OMP supplies bundled agents; this repository deliberately does not shadow them
locally. Its tracked custom-agent surface in `agent/agents/` is limited to
judgment and specialist roles: `kiss`, `designer`, `operator`,
`innovation-council`, `innovation-challenger`,
`experiment-design`, and `experiment-peer`.

The boundaries are intentional:

- **Configuration** selects models and runtime defaults (`agent/config.yml`).
- **Agents** provide isolated execution or judgment roles (`agent/agents/`).
- **Skills** package reusable workflows (`agent/skills/`).
- **Extensions** implement runtime mechanics (`agent/extensions/`);
  `extension-health.ts` reports registration health, not runtime correctness.

`agent-rails.ts` requires `skill://git-tool` for branch and integration commands,
including commands with Git global options, shell separators and known command
wrappers. Shell `-c` and `eval` commands are inspected recursively within parser
bounds; `cd` and `pushd` select the destination repository's policy. Unresolved
directories fail closed for publishing. Quoted `echo` and search arguments do
not trigger the check. Arbitrary interpreter, xargs/find and shell-write detection
are not promised.
Record `gate.json` policies with `agentMerge: "disabled"` require owner
approval for `git push`, `gh pr create` and `gh pr merge`; invalid policies fail
closed for those actions. All-session handoff, including subagents, stays blocked
until verification references are read after that session's code edits and failed
`pathCheck` paths pass on retry. The check covers edited paths plus prior failures
that `git status` reports as changed or untracked, not every repository file.
Deleted, ignored, unchanged/restored and Git-directory paths are dropped; a failed
`git status` query fails the check. Handoff runs no suite.

With dynaflows on, rails guard `gh pr merge`: CI must be green on the PR's exact
head, and `prove()`'s result file `<git-common-dir>/omp-prove/<head>.result.json`
must say `status: pass` for that head. That file is the one pass signal. The repo's
record `gate.json` is the policy authority: when it declares `ci`,
`ci.requiredContext` must be `SUCCESS` on the exact head as a commit status or
check run, whoever posts it.

Repository checks follow the test tiers. The record's `gate.json` (schema version 2) declares
`checks.commit` (static checks, all T1 tests and T2 seams only for affected code) and
`checks.ci` (the full T1 and T2 suite), each naming the repo's own command. At each agent
commit, rails run the record's `pathCheck`, then `checks.commit`, and then the harness
checks: `omp-guardrails check`, run from this checkout with `--repo <worktree>` and `--record
<record dir>`, over the whole repo. Any finding blocks the commit; there are no baselines and no
diff scoping, and scope exclusions come from the record. The CI runner named by `ci.runner` runs
`checks.ci` for every push and merge. Optional `checks.format` is the formatter `freeze()` runs
before hashing. Pulling existing commits into a branch (merge, rebase, cherry-pick, revert,
pull) runs no harness check: those commits were checked when they were made, and CI checks the
result. Rails block hook bypass flags/settings (`--no-verify`, `-n`, `core.hooksPath`) on Git
commands. Standalone executable-shell assignment, `export`/`declare`, `unset` and `env -u` of
hook-skip variables are blocked even without a Git action, closing the earlier persistent-shell
assignment bypass. Quoted messages are not assignments; inherited external shell state is not a
sandbox guarantee. Only schema version 2 is accepted.

This repository's hook is `.githooks/pre-commit`, which runs its own commit tier
(`run-ci-quality-gates.sh --tier commit --fix --stage`). Enable it once per clone
with `git config core.hooksPath .githooks`.

Dynaflows switches on the [canonical six-stage workflow](agent/skills/omp-dyna-workflows/SKILL.md).
Use an entire standalone main-prompt line `$dynaflows`, `$dynaflows on` or
`$dynaflows off`; prose/quoted mentions do not toggle it. The switch is the record's
`dynaflows.enabled`; linked worktrees share their main checkout's record. No `origin`
remote or no record means off, and a machine default `~/.omp/dynaflows.json` of
`{"enabled": false}` turns it off for every repo. With dynaflows on, rails inject the
workflow pointer, require guardrail owner approval and guard `gh pr merge` as above.
Subagent filesystem edit paths must be absolute and cannot escape enclosing Git
roots through symlinks; outside-repo scratch paths and internal URIs are allowed.
Selecting the assigned worktree remains an instruction, not a path-rail guarantee.
Proofs are outcome tests tied to the repo's feature map (the record profile's
`verification_surface.feature_map_path`): `freeze()` refuses a proof file no map doc links, and
`prove()` blocks a map doc changed in the PR that links no frozen proof. Behaviour found during
implementation, or by the map sweep before the PR, gets a map entry and a test, then a re-freeze.
Helper contracts live in [patterns](agent/skills/omp-dyna-workflows/references/patterns.md);
Git/PR/CI/merge authority lives in `skill://git-tool`.

omp auto-discovers every module in `agent/extensions/`. To disable one, add
`disabledExtensions: [extension-module:<name>]` to `agent/config.yml` and remove
its path from `extensions:`; an explicitly listed path loads even when disabled.

`.gitignore` is an allowlist for the canonical source kept here. Runtime and
update-managed state is ignored by default, including state that may appear
beside tracked files. Treat only the explicitly reopened paths as
repository-owned configuration; do not mistake local runtime assets for tracked
source.

## Per-repo records

A repo never depends on, contains or mentions omp or dynaflows. Its harness settings
live in a **record**: `repos/<owner>/<name>/` under this checkout's root, lowercase,
named by the clone's `origin` remote (`https://github.com/<owner>/<name>(.git)`,
`git@github.com:<owner>/<name>(.git)` or `ssh://git@github.com/<owner>/<name>(.git)`).
Worktrees resolve to the same record as their main checkout.

- `gate.json` holds the repo's own commands (`checks.commit`, `checks.ci`,
  `pathCheck`), the `ci` contract, `agentMerge` and `dynaflows.enabled`.
  `pathCheck` and `checks.commit` are required; a record without either, or with a
  field outside the schema, fails to load with an error naming the field.
- `profile.md` holds the repo test profile, including every approval and exception.

`/repos/` is gitignored: records are never committed, because they mean nothing to
anyone else who takes this config. `scripts/device-rollout.py` copies them to
devices. `skill://setup-repository-guardrails` drafts a record for owner
confirmation and writes nothing into the target repo except approved fixes.
A record is the owner's alone: rails refuse every agent edit-tool write under `repos/`
(a lexical check on the resolved path), so the owner's hand edit is its only review.
Bash writes to records are not guarded. When dynaflows resolves off, rails say why
once at session start and at every commit, naming the slug (or `none`) and the
reason; `workflows.py` uses the same words when it refuses. Rails and `workflows.py`
strip the harness's `AGENT` variable from every repo command they run.

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

Run `python3 scripts/device-rollout.py` from this Mac to fetch and report local
`~/.omp` and `~/.agents` revisions (never pull either local checkout), fast-forward
clean remote `~/.agents` checkouts, and start the NUC OMP deployment service.
It also writes each device's untracked `~/.omp/ci-runners.json` from the runner
role definitions in `scripts/device-rollout-hosts.json`.
It copies the ignored `repos/` records one way from this device into the `repos/`
folder of every target device that runs omp (a registry entry with an `omp` path),
and refuses to overwrite a target record that is newer, naming it.
On each reachable device it also streams the judge key from Infisical into
`omp auth import -` over stdin, and runs an `omp find` canary whose coverage fills
the `find` column. Untracked remote files are preserved; tracked changes block
pulls. Use `--check` to fetch and report only, without pulls, service starts, key
provisioning, or record copies. The table exits nonzero for stale, failed,
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
gitignored `repos/` records. If `nuc-config` lacks `main`, sync it (see
[`.omp/AGENTS.md`](.omp/AGENTS.md)), then rerun the command.
The script never merges or pushes. Oh-mi-pi-config is not deployed to mac-cf2;
only its `~/.agents` checkout is a rollout target, so judge-key provisioning and
the find canary run only on devices that run omp.

## CI runners

The record's `gate.json` declares what must pass, not where it runs:
`"ci": {"requiredContext": "gate", "runner": "ci-gate"}`. Rails and `prove()`
check only that the required context is green on the exact head, whoever posts
it. The runner is a role name. Each device's untracked `~/.omp/ci-runners.json`,
written by `scripts/device-rollout.py`, maps roles to submit, wait, status and logs
commands. Push an `agent/<slug>` branch, then submit and wait:

```sh
python3 ~/.omp/scripts/ci-runner.py submit ci-gate OWNER/REPO SHA
python3 ~/.omp/scripts/ci-runner.py wait ci-gate OWNER/REPO SHA
```

Replace `SHA` with the pushed 40-character lowercase commit SHA. `status` prints
the result; `logs` prints output (`--tail N` limits it). `wait` defaults to the
role's `timeout` from the tracked role config; `--timeout` overrides it. A role
without configured `wait`/`status` commands uses a built-in GitHub poller and
needs `--context` set to the gate's `ci.requiredContext`; its timeout exits 124.
The `github-actions` role starts on push, so `submit` only reports that. A
missing role exits 2 and names the device-rollout command to run. This
repository's runner specifics and fallback live in [`.omp/AGENTS.md`](.omp/AGENTS.md).

### ci-gate service

The `ci-gate` role is `scripts/ci-gate.py` on the owner's Linux NUC, running
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
The runner executes `checks.ci` after the devcontainer's `postCreateCommand`.

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
fails before creating job state when either is unset. `scripts/device-rollout.py`
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
not a required context. The runner parity check in `run-ci-quality-gates.sh` reads
only `.githooks/pre-commit`, so the workflow can change or go without breaking the gate.
Dispatch manually from Actions → CI quality gates → Run workflow, or:

```sh
gh workflow run ci-quality-gates.yml --repo adam-jackson-cf/oh-mi-pi-config --ref main
```

## Repository map

- `agent/config.yml` — model-role routing and OMP runtime defaults.
- `agent/AGENTS.md` — engineering, verification, and response rules.
- `agent/agents/` — the repository's custom evaluation and advisory roles.
- `agent/skills/omp-config-optimise/` — model-routing preferences and the
  evidence-driven process for re-tuning `agent/config.yml`.
- `agent/skills/setup-repository-guardrails/` — repository guardrail setup. Its
  `omp-guardrails` Python reference ships the rule catalogue
  (`omp_guardrails/data/guardrail-rules.json`). The harness runs it from this
  checkout over each repository from outside its tree. Its own tests prove every rule
  it implements.
  `assets/example-record/` is the tracked generic per-repo record.
- `agent/lib/repo_record.py` and `agent/lib/repo-record.ts` — the origin-to-record
  resolver for Python and TypeScript callers.
- `repos/<owner>/<name>/` — personal per-repo records; gitignored, never committed.
- `agent/extensions/` — tracked runtime extensions, including extension-health
  reporting.
- `agent/lsp.json` — LSP configuration.
- `scripts/check-paths.py` — the gate.json `pathCheck` for the source allowlist and test layout.
  Path arguments are relative to the working directory; no arguments checks all tracked files.
- `scripts/ci-runner.py` — runs a CI runner role's submit, wait, status or logs
  command from `~/.omp/ci-runners.json`.
- `scripts/ci-gate.py` and `scripts/ci-gate-repos.json` — `ci-gate` job service and
  repository/token-name allowlist.
- `.omp/AGENTS.md` — this repository's project context: CI runner specifics,
  fallback and how a nuc-config sync merges.
- `.devcontainer/` — pinned gate image and dependency installation.
- `tests/test_ci_gate.py` — isolated CLI, container, and GitHub-status contracts.
- `.gitignore` — canonical-source allowlist and runtime-state boundary.
