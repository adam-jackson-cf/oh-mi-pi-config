---
name: "omp-config-optimise"
description:
  "USE WHEN reviewing or changing OMP model routing (modelRoles, agent models, reasoning effort,
  service tiers) after new models, price or quota changes, or performance evidence."
---

# Optimise OMP model routing

Keep `~/.omp/agent/config.yml` routing aligned with the owner's preferences and
current evidence, without asking the owner to restate either.

## Read first

- [Owner preferences](references/preferences.md) — objective, role placement,
  reasoning caps. Binding.
- [Current routing](references/routing.md) — the live decision table and the
  evidence behind each row.
- [OMP mechanics](references/omp-mechanics.md) — verified resolution rules and
  traps (effort precedence, `:off`, caps, recursion depth).
- [Pricing and quota](references/pricing-and-quota.md) — sources and the last
  recorded figures.

## Procedure

Follow [the process](references/process.md): snapshot evidence
(`scripts/snapshot.ts`), find what changed, decide against the preferences and
invariants, test capability claims with [an experiment](references/experiments.md),
apply, then verify in a fresh process.

## Boundaries

- Never print credentials or raw provider responses; use `--redact` on usage.
- Do not edit preferences; propose changes to the owner.
- Commits and pushes need separate explicit permission.
