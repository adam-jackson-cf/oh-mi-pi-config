---
name: "omp-config-optimise"
description:
  "Use when reviewing or changing OMP model routing, agent models, reasoning effort or service
  tiers after new models, price or quota changes, or performance evidence."
---

# Task

## Load the owner's standing context

- **Purpose**: Keep `~/.omp/agent/config.yml` routing aligned with the owner's preferences and
  current evidence, without asking the owner to restate either.
- **When**: Run first in every routing review or change.
- Read [Current routing](references/routing.md) for the live decision table and its evidence.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- Runbook: [Owner preferences](references/preferences.md)

## Run the optimisation process

- **Purpose**: Snapshot evidence, find what changed, decide against the preferences and
  invariants, apply, then verify in a fresh process.
- **When**: A model launches or retires, prices or quotas change, or a role underperforms.
- Check [OMP mechanics](references/omp-mechanics.md) and
  [Pricing and quota](references/pricing-and-quota.md) before deciding.
- Runbook: [Optimisation process](references/process.md)

## Test capability claims

- **Purpose**: Support a capability claim with a frozen experiment before routing on it.
- **When**: A routing decision depends on a claim about model behaviour.
- Runbook: [Experiments before routing](references/experiments.md)
