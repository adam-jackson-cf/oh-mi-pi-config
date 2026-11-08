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
fixed-plan work; Claude Sonnet 5.5 designs and verifies; GPT-6 Luna handles
simple actions. The verifier runs in an independent session using a different
model family from every author. Reasoning above medium on Opus or Astra is reserved
for the `innovation-council` (Opus-led, Astra challenger) and
`experiment-design` (Fable-led, Astra and Opus peers).

## Ownership boundaries

OMP supplies bundled agents; this repository deliberately does not shadow them
locally. Its tracked custom-agent surface in `agent/agents/` is limited to
judgment and specialist roles: `verifier`, `kiss`, `designer`, `operator`,
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
Repository `.omp/gate.json` policies with `agentMerge: "disabled"` require owner
approval for `git push`, `gh pr create` and `gh pr merge`; invalid policies fail
closed for those actions. All-session handoff, including subagents, stays blocked
until verification references are read after that session's code edits and failed
`pathCheck` paths pass on retry (deleted paths are dropped). The check covers edited
paths plus previous failures, not every repository file; handoff runs no suite.

Repository checks follow the test tiers. `.omp/gate.json` schema version 2 declares
`checks.commit` (static checks, all T1 tests and T2 seams only for affected code)
and `checks.ci` (the full T1 and T2 suite). The Git pre-commit hook runs
`checks.commit`; CI on `ciHost` runs `checks.ci` for every push and merge.
For `git commit`, rails blocks explicit hook bypass flags/settings and requires an
executable pre-commit hook when `checks.commit` is declared. Supported merge/rebase
forms also reject explicit hook disabling with command-specific flag semantics.
Standalone executable-shell assignment, `export`/`declare`, `unset` and `env -u`
of hook-skip variables are blocked even without a Git action, closing the earlier
persistent-shell assignment bypass. Quoted messages are not assignments; inherited
external shell state is not a sandbox guarantee. Only schema version 2 is accepted.

This repository's hook is `.githooks/pre-commit`, which runs `checks.commit`
(`run-ci-quality-gates.sh --tier commit --fix --stage`). Enable it once per clone
with `git config core.hooksPath .githooks`.

Dynaflows switches on the [canonical six-stage workflow](agent/skills/omp-dyna-workflows/SKILL.md).
Use an entire standalone main-prompt line `$dynaflows`, `$dynaflows on` or
`$dynaflows off`; prose/quoted mentions do not toggle it. The primary checkout's
`.omp/dynaflows.json`, resolved through the Git common directory, is shared by
linked worktrees and overrides the per-machine `~/.omp/dynaflows.json`; default is
off. The local override is excluded through the common Git directory. Non-repo
prompts do not create repository overrides. With dynaflows on, rails injects the
workflow pointer, requires guardrail owner approval and enforces verifier family
independence against persistent per-change recorded repository editors/committers,
scoped to real worktree and branch. Arbitrary bash-written authors are not detected.
Subagent filesystem edit paths must be absolute and cannot escape enclosing Git
roots through symlinks; outside-repo scratch paths and internal URIs are allowed.
Selecting the assigned worktree remains an instruction, not a path-rail guarantee.
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

## NUC branch (`nuc-config`)

This branch is the configuration for the Linux NUC (`adam-ai@omarchy.tailf3b31b.ts.net`).
Merge `main` into `nuc-config` to
take upstream changes while retaining these Linux settings:

- `agent/AGENTS.md` — the `~/Projects/` folder-structure rule, so work started
  remotely on the NUC (worktrees included) lands in the right category folder.
- `agent/lsp.json` and `.omp/lsp.json` — language servers are bare command names
  resolved on `PATH`, not macOS install paths. `pyright` and
  `typescript-language-server` are installed with the mise Node
  (`npm install -g pyright typescript-language-server typescript`).
  `rust-analyzer` is not installed; the server stays inactive until it is.

### Managed NUC source and runtime

`~/Projects/tools/omp-nuc-config` is the clean `nuc-config` Git checkout.
`~/.omp` is the deployed runtime, containing private authentication, sessions,
and generated telemetry configuration. Never run Git or mirror with deletion in
`~/.omp`. The source checkout stays clean; agent-introspection applies its managed
telemetry overlay only to the runtime.

`scripts/omp-config-sync.py` replaces the copy-only `~/.local/bin/omp-config-pull`
script. It locks syncs, requires a clean `nuc-config` checkout, and pulls with
`--ff-only`. It refuses local edits to tracked runtime files using stored SHA-256 hashes, then
copies source changes without `.git` and removes retired tracked
files only when their bytes match the previous deployed source revision. A locally
modified retired file stops deployment before runtime changes; preserve or resolve
that file before retrying. Untracked authentication, sessions, and local skills
remain in place. Private backups, expected runtime hashes, and the deployed revision live under
`~/.local/state/omp-config-sync` with directory mode `0700` and backup files `0600`.
Backups contain only files changed by deployment; unchanged daily runs create none.
The helper records hashes after deployment and after the managed telemetry command,
including command failure, so retries preserve expected overlay changes. Unknown
local edits stop sync; preserve them and reconcile with canonical source before retrying.
Before writing runtime files, the helper saves a private `pending.json` journal.
An interrupted deployment resumes only bytes matching the known old or intended new
source hashes. Unknown edits still stop recovery. Recovery runs before any pull and
requires source HEAD to match the journal target; restore that revision if HEAD moved.
After deployment state is saved, the journal is removed. SIGTERM permits the
managed-overlay cleanup to record hashes. SIGKILL or power loss during the external
overlay can leave unknown bytes; inspect and reconcile those files before retrying.
Corrupt state or unavailable history stops with an explicit recovery message; restore
the last deployed revision rather than adopting unknown runtime bytes.
On first use, run before updating the source manually: the pre-pull source revision
provides the retirement baseline. For an already updated source, seed
`deployment.json` in that private state directory with `{"revision":"<old-commit>"}`
using the last deployed source commit, before running the helper. Bootstrap expects
tracked runtime files to match that source commit. Inspect and reconcile any
mismatch first; only seed reviewed expected hashes for an explicitly approved
managed overlay. Never adopt unknown runtime bytes automatically.

The CLI deploys only to `~/.omp`; it is not a staging command because telemetry
configuration targets the home runtime.

Requirements: Python 3, Git, Bun **1.3.0** at
`~/.local/share/mise/installs/bun/1.3.0/bin/bun`, and
`~/.local/bin/agent-introspection`. Dependency installation uses the explicit Bun
path, adds it to `PATH`, and sets `SHARP_IGNORE_GLOBAL_LIBVIPS=1`. The repository's
OMP development dependency is **18.16.0**; keep the separately installed NUC OMP
binary at or above that version. Check with `omp --version` and update with
`mise upgrade github:can1357/oh-my-pi`. The helper does not update the OMP binary.
Restart existing OMP sessions after a successful deployment.

Install the templates from `scripts/systemd/` into `~/.config/systemd/user/`,
replace the old copy-only service if present, then run `systemctl --user daemon-reload`
and `systemctl --user enable --now omp-config-pull.timer`. The service is restricted
to Linux hostname `omarchy` and runs the versioned helper directly. Run
`systemctl --user start omp-config-pull.service` for an immediate sync and inspect
`systemctl --user status omp-config-pull.service` for completion. These are user
services. Check `loginctl show-user adam-ai -p Linger` returns `Linger=yes` so
the user manager runs after logout and reboot. Linger is already enabled on the
NUC; enable it with `loginctl enable-linger adam-ai` only if the check fails.
The service bounds subprocess runtimes and its total runtime, and retries failures
after one minute, up to three starts per hour, before the next daily timer run.
After correcting a failure that exhausted this limit, run
`systemctl --user reset-failed omp-config-pull.service`, then start the service again.

Not carried by this repository, so set up on the NUC separately:

- `~/.agents` is a Git clone of the private `adam-jackson-cf/skills-directory`
  repository, managed by the owner. It uses the read-only deploy key
  `~/.ssh/id_ed25519_skills_directory_ro` through the SSH config alias
  `github-skills-directory` (`git@github-skills-directory:adam-jackson-cf/skills-directory.git`).
  Skills not tracked there, including the NUC-only `skills/omarchy` and
  `skills/diagnose-crash` links, remain local to the NUC.
- The OpenRouter credential: `omp` resolves to the mise shim, with no wrapper.
  The key is omp's stored credential, imported from Infisical (project `dev`,
  environment dev) with `omp auth import -`; `omp token openrouter` confirms it.
  Agent Vault is retired.

## Device rollout

Run `python3 scripts/device-rollout.py` from this Mac to fetch and report local
`~/.omp` and `~/.agents` revisions (never pull either local checkout), fast-forward
clean remote `~/.agents` checkouts, and start the NUC OMP deployment service.
Untracked remote files are preserved; tracked changes block pulls. Use `--check`
to fetch and report only, without pulls or service starts. The table exits nonzero
for stale, failed, unreachable, dirty-blocked, or behind-`main` deployments.
Hosts and paths live in `scripts/device-rollout-hosts.json`.
Local rows use the registry's `local` mapping for the lowercase short hostname
(or that hostname itself when unknown), and the local device is skipped as a remote target.

The NUC OMP runtime is managed only by `~/Projects/tools/omp-nuc-config` on
`nuc-config` and `omp-config-pull.service`; this command never runs Git in or
copies files into its `~/.omp`. If `nuc-config` lacks `main`, open an
`agent/nuc-sync-<date>` PR incorporating `main` into `nuc-config`, using a
**merge commit** under the NUC sync exception in
`skill://git-tool/references/merge-and-recover.md`, then rerun the command.
The script never merges or pushes. Oh-mi-pi-config is not deployed to mac-cf2;
only its `~/.agents` checkout is a rollout target.

## NUC gate (CI)

The owner's Linux NUC runs exact-SHA gates in rootless Podman with gVisor.
Only repositories in `scripts/nuc-gate-repos.json` can run. Push an
`agent/<slug>` branch, then submit and wait over SSH:

```sh
ssh codex-omarchy-nuc \
  'python3 ~/.omp/scripts/nuc-gate.py submit adam-jackson-cf/oh-mi-pi-config SHA'
ssh codex-omarchy-nuc \
  'python3 ~/.omp/scripts/nuc-gate.py wait adam-jackson-cf/oh-mi-pi-config SHA --timeout 1200'
```

Replace `SHA` with the pushed 40-character lowercase commit SHA.

`status` prints the result; `logs` prints output (`--tail N` limits it).
Resubmitting a running SHA does not launch another job. Successful and failed
gate results stay cached; an infrastructure `error` is retried on resubmission.
`wait` returns only after final GitHub status publication finishes. It exits 0
for success, 1 for gate failure/error or status-publication failure, and 2 when its
wait expires. Publication failures preserve the gate outcome in `result.json`,
add a `publicationError`, and print the publication error to stderr.
`.omp/gate.json` declares `ciHost: "nuc"`: the NUC is this repository's CI
host, posting GitHub commit status context `gate`. `ciHost: "github"` (the
default) means the repository's GitHub Actions; personal projects use it.
The runner executes `checks.ci` (schema version 1: `gate`) after the
devcontainer's `postCreateCommand`.

Logs and atomic `result.json` files remain under
`${XDG_STATE_HOME:-~/.local/state}/nuc-gate/jobs/<owner>/<repo>/<sha>/`;
checkouts are removed after completion. Bare mirrors live under
`${XDG_DATA_HOME:-~/.local/share}/nuc-gate/mirrors/`. Jobs default to one
global slot (`NUC_GATE_SLOTS`), with a 1,200-second systemd runtime limit
(`NUC_GATE_TIMEOUT`). Retained logs/results and cached images need owner-managed
retention; the service never globally prunes Podman resources.

Infisical injects `NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF` into host-side fetches
and the status publisher, never the container. Allowlisted public and private
repositories are supported, including submissions from SSH shells without a
token. Grant the NUC identity access to that secret in project
`052b4c81-b9cc-4694-9019-d131ec3817cc`, environment `dev`. The fine-grained GitHub
token needs access to every opted-in repository (or **All repositories**) with
**Contents: read** and **Commit statuses: write** permissions. Missing credentials
leave local results intact but make publication exit nonzero. Public fetches
need no token; authenticated fetches use GitHub Basic authentication through
process-local Git configuration environment variables, never command-line
arguments or saved mirror configuration.

`NUC_GATE_INFISICAL_PROJECT`, `NUC_GATE_INFISICAL_ENV`, `NUC_GATE_REPOS`,
`NUC_GATE_GITHUB_API`, and executable overrides `NUC_GATE_GIT`,
`NUC_GATE_PODMAN`, `NUC_GATE_SYSTEMD_RUN`, `NUC_GATE_INFISICAL` support
deployment and isolated tests. Runtime flags are JSON arrays:
`NUC_GATE_RUNTIME_FLAGS` defaults to
`["--runtime", "/usr/local/bin/runsc", "--runtime-flag=ignore-cgroups"]`;
`NUC_GATE_BUILD_RUNTIME_FLAGS` defaults to `[]` (Podman's default build runtime).
On the NUC, rootless Podman 6.1.4 passes `--no-new-keyring` to its build runtime,
which runsc rejects. The default-runtime build succeeded, followed by two full
gates under runsc with GitHub pending/success statuses. Builds run only the
repository's own Containerfile from the owner's allowlisted repositories;
gate execution stays inside gVisor. Images are cached by Dockerfile-content hash.

GitHub quality gates are a weekly Monday 04:23 UTC fallback plus manual
`workflow_dispatch`, not push/PR CI. Dispatch manually from Actions → CI quality
gates → Run workflow, or:

```sh
gh workflow run ci-quality-gates.yml --repo adam-jackson-cf/oh-mi-pi-config --ref main
```

## Repository map

- `agent/config.yml` — model-role routing and OMP runtime defaults.
- `agent/AGENTS.md` — engineering, verification, and response rules.
- `agent/agents/` — the repository's custom evaluation and advisory roles.
- `agent/skills/omp-config-optimise/` — model-routing preferences and the
  evidence-driven process for re-tuning `agent/config.yml`.
- `agent/extensions/` — tracked runtime extensions, including extension-health
  reporting.
- `agent/lsp.json` — LSP configuration.
- `scripts/check-paths.py` — the gate.json `pathCheck` for the source allowlist and test layout.
  Path arguments are relative to the working directory; no arguments checks all tracked files.
- `scripts/nuc-gate.py` and `scripts/nuc-gate-repos.json` — NUC job service and
  repository/token-name allowlist.
- `.omp/gate.json` — gate commands and declared CI host.
- `.devcontainer/` — pinned gate image and dependency installation.
- `tests/test_nuc_gate.py` — isolated CLI, container, and GitHub-status contracts.
- `.gitignore` — canonical-source allowlist and runtime-state boundary.
