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
`experiment-design`, and `experiment-peer`. `lsp-evidence` and its policy
extension are local runtime assets, not tracked canonical source.

The boundaries are intentional:

- **Configuration** selects models and runtime defaults (`agent/config.yml`).
- **Agents** provide isolated execution or judgment roles (`agent/agents/`).
- **Skills** package reusable workflows (`agent/skills/`).
- **Extensions** implement runtime mechanics (`agent/extensions/`);
  `extension-health.ts` reports registration health, not runtime correctness.

`skill-gate.ts` requires `skill://git-tool` for branch and integration commands,
including commands with Git global options, shell separators and known command
wrappers. Shell `-c` commands are inspected recursively; `cd` and `pushd` select
the destination repository's policy. Unresolved directories fail closed for
publishing. Quoted `echo` and search arguments do not trigger the gate.
Repository `.omp/gate.json` policies with `agentMerge: "disabled"` require owner
approval for `git push`, `gh pr create` and `gh pr merge`; invalid policies fail
closed for those actions. Main-session handoff stays blocked until
verification references are read, the exact `fastGate` has run after edits (any
exit code), and failed `pathCheck` checks pass on retry (deleted paths are dropped).
Working-tree fingerprints also detect delegated edits, including untracked file
content and staged changes before the first commit; unchanged pre-existing dirt
does not block Q&A. Fingerprinting uses two Git calls and reads at most 1,024
untracked files totalling 16 MiB. Git/filesystem failures and those bounds disable
only fingerprint enforcement, not explicit edit/reference/path checks.

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
Resubmitting a running or completed SHA does not launch another job. `wait`
exits 0 for success, 1 for failure/error, and 2 when its wait expires.
`.omp/gate.json` declares `ciHost: "nuc"`: the NUC is this repository's CI
host, posting GitHub commit status context `gate`. `ciHost: "github"` selects
GitHub as the declared CI host; the field is metadata, not automatic routing.
The full `gate` argv runs after the devcontainer's `postCreateCommand`.

Logs and atomic `result.json` files remain under
`${XDG_STATE_HOME:-~/.local/state}/nuc-gate/jobs/<owner>/<repo>/<sha>/`;
checkouts are removed after completion. Bare mirrors live under
`${XDG_DATA_HOME:-~/.local/share}/nuc-gate/mirrors/`. Jobs default to one
global slot (`NUC_GATE_SLOTS`), with a 1,200-second systemd runtime limit
(`NUC_GATE_TIMEOUT`). Retained logs/results and cached images need owner-managed
retention; the service never globally prunes Podman resources.

Infisical injects `NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF` into the host-side
publisher, never the container. Grant the NUC identity access to that secret in
project `052b4c81-b9cc-4694-9019-d131ec3817cc`, environment `dev`, with
repository commit-status write permission. Missing credentials leave local results
intact but make publication exit nonzero. Pending publication also uses Infisical
when the SSH shell has no token. Public fetches need no token; authenticated
fetches use a command-local Git `http.extraHeader`, never saved configuration.

`NUC_GATE_INFISICAL_PROJECT`, `NUC_GATE_INFISICAL_ENV`, `NUC_GATE_REPOS`,
`NUC_GATE_GITHUB_API`, and executable overrides `NUC_GATE_GIT`,
`NUC_GATE_PODMAN`, `NUC_GATE_SYSTEMD_RUN`, `NUC_GATE_INFISICAL` support
deployment and isolated tests. Runtime flags are JSON arrays:
`NUC_GATE_RUNTIME_FLAGS` and `NUC_GATE_BUILD_RUNTIME_FLAGS` default to
`["--runtime", "/usr/local/bin/runsc", "--runtime-flag=ignore-cgroups"]`.
Run flags worked on the NUC; build flags have **not** been tested there
(the spike built with Podman's default build runtime). Test that Podman/Buildah
combination before deployment; a build failure is an infrastructure error, not
a silent fallback. Images are cached by Dockerfile-content hash.

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
