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
`agent/AGENTS.md` and `agent/WATCHDOG.yml` hold the durable operating policy;
the advisor is intended to challenge disproportionate solution complexity, not
replace those rules.

`agent/config.yml` routes work through named `modelRoles`, so model choice
follows the job rather than ad hoc selectors. It also defines runtime defaults
such as edit diagnostics, task LSP, checkpoints, compaction, and managed skill
locations.

## Ownership boundaries

OMP supplies bundled agents; this repository deliberately does not shadow them
locally. Its tracked custom-agent surface is limited to independent judgment
roles in `agent/agents/`: `completionist`, `plan-judge`, `test-judge`,
`innovation-council`, and `innovation-fable`. Reusable coordination lives
instead in the `agent/skills/orchestrate/` skill.

When its workflow calls for it, `orchestrate` may delegate `lsp-evidence`; its
current agent and policy extension are local runtime assets, not tracked
canonical source.

The boundaries are intentional:

- **Configuration** selects models and runtime defaults (`agent/config.yml`).
- **Agents** provide isolated execution or judgment roles (`agent/agents/`).
- **Skills** package reusable workflows (`agent/skills/`).
- **Extensions** implement runtime mechanics (`agent/extensions/`);
  `extension-health.ts` reports registration health, not runtime correctness.

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

## Repository map

- `agent/config.yml` — model-role routing and OMP runtime defaults.
- `agent/AGENTS.md` — engineering, verification, and response rules.
- `agent/WATCHDOG.yml` — review and escalation policy.
- `agent/agents/` — the repository's custom evaluation and advisory roles.
- `agent/skills/orchestrate/SKILL.md` — supervised coordination workflow.
- `agent/extensions/` — tracked runtime extensions, including skill filtering
  and extension-health reporting.
- `agent/skill-auto-whitelist.json` — explicit user-skill availability policy.
- `agent/lsp.json` — LSP configuration.
- `.gitignore` — canonical-source allowlist and runtime-state boundary.
