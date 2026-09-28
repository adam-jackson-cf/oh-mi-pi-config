# Current routing

Last reviewed 2026-09-28. Source of truth is `~/.omp/agent/config.yml`; this
list records why. Update both together.

## Roles

- `default` + `defaultThinkingLevel: medium` → Opus 5.5 : medium. Used by main session.
  Orchestrator; `auto` cannot be capped at medium.
- `plan` → Opus 5.5 : medium. Used by plan mode. Preference; rigorous plans go to the council.
- `slow` → Opus 5.5 : medium. Used by model cycling. Preference cap.
- `designer` → Opus 5.5 : medium. Used by `designer` agent. Preference.
- `reviewer` → Opus 5.5 : medium. Used by bundled `reviewer`. Opus reviews Luna code across
  families. Resolved Opus:medium on 2026-09-28; whether via this role or parent inheritance is
  unconfirmed.
- `task` + Fast tier → GPT-6 Luna : medium. Used by bundled `task`. Experiments 2026-09-28: `low`
  suffices for simple plans (18/18) but missed on multi-file plans (7/9 vs `medium` 9/9, at equal
  cost).
- `smol` → GPT-6 Luna : low. Used by `sonic` (Fast), `scout`, `lsp-evidence`, prewalk. Lowest
  effort; mechanical work.
- `tiny`, `commit` → GPT-6 Luna : low. Used by titles, commit flow. Cheapest; replaced missing
  `gpt-5.4-mini`.
- `web` → GPT-6 Luna. Used by `web_search`. Verified working 2026-09-28.
- `plan-judge`, `test-judge`, `completionist` → GPT-6 Sol : high. Used by judges. Other family from
  the Opus and Luna authors; Sol costs 1/5 of Astra.
- `kiss` → GPT-6 Sol : medium. Used by `kiss` agent. Other family from the Opus planner.
- `vision` → GPT-6 Sol : medium. Used by image reading. Image input; cheaper than Terra.
- `advisor` → GPT-6 Astra : low. Used by advisor watchdog runtime alongside the Jev classifier
  extension. Owner-fixed; not a routing lever. Leave unchanged in reviews.
- `innovation-council` → Opus 5.5 : high. Used by council lead. Owner: Opus-led council.
- `innovation-challenger` → GPT-6 Astra : high. Used by council and experiment-design peer.
  Cross-family challenger.
- `experiment-design` → Fable 5.1 : high. Used by experiment lead. Owner: Fable leads novel-problem
  framing.
- `experiment-peer` → Opus 5.5 : high. Used by experiment-design peer. Owner: Opus participates.

## Service tiers

`task.agentServiceTierOverrides`: `task` and `sonic` use `priority` (Fast):
2.5× Codex credits, measured ~20% lower wall time on the fixture set.

## Watch items

- Fable draws the shared Anthropic weekly limit faster than Opus and is capped
  at 50% of it; keep `experiment-design` for genuinely novel problems.
- GPT-5.5 retires from Codex on 2026-10-14; no route uses it.
- `task: medium` rests on 9 trials per arm across three hard fixtures; rerun
  the hard set when Luna changes or subagent implementation failures rise.
- Open-ended work handed to `task` without a plan under-delivers at low effort
  (a fixture-building task returned fixtures at a fifth of the requested size);
  the orchestrator should plan before delegating to `task`.
