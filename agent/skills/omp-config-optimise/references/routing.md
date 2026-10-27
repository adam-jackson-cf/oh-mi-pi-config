# Current routing

Last reviewed 2026-10-27 (GPT-6.1 Terra launch, OMP 18.13.1). Source of truth is
`~/.omp/agent/config.yml`; this list records why. Update both together.

## Roles

- `default` + `defaultThinkingLevel: medium` → Opus 5.5 : medium. Used by main session.
  Orchestrator; `auto` cannot be capped at medium.
- `plan` → Opus 5.5 : medium. Used by plan mode. Preference; rigorous plans go to the council.
- `slow` → Opus 5.5 : medium. Used by model cycling. Preference cap.
- `designer` → Sonnet 5.5 : medium. Used by `designer` agent. Owner decision 2026-09-30; no design
  experiment exists.
- `reviewer` → GPT-6.1 Sol : low. Used by bundled `reviewer` through
  `task.agentModelOverrides.reviewer: "@reviewer"`; without the override it inherited the parent's
  model (fresh-process check 2026-09-28). Review-detection 2026-09-30: 6.1 Sol `low` 36/36, 0 clean
  FP, $0.059, 76 s; GPT-6 Sol `medium` 36/36, 0.33 FP, $0.072, 109 s. Independence comes from the
  fresh subagent session, not the model family.
- `task` → Sonnet 5.5 : low, standard tier. Used by bundled `task`. Hard fixed-plan set: Sonnet
  `low` 9/9, $0.112, 25 s median, 5 turns (2026-09-28); GPT-6.1 Sol `low` 9/9, $0.122, 238 s, 16
  turns (2026-09-30). Cost includes caching. Owner kept Sonnet 2026-09-30 for speed (split-family
  review was also a reason then; no longer required since 2026-10-02); it also keeps implementation
  load on the Anthropic quota.
- `smol` → GPT-6 Luna : low. Used by `sonic` (Fast), `scout`, `lsp-evidence`, prewalk. Lowest
  effort; mechanical work.
- `tiny`, `commit` → GPT-6 Luna : low. Used by titles, commit flow. Cheapest; replaced missing
  `gpt-5.4-mini`.
- `web` → GPT-6 Luna. Used by `web_search`. Verified working 2026-09-28.
- `plan-judge`, `test-judge`, `completionist`, `kiss` → GPT-6.1 Sol : low. Used by judges.
  Review-detection 2026-09-30 (above); `plan-judge` and `kiss` by extrapolation (the harness judges
  code, not plans).
- `vision` → GPT-6.1 Terra : low. Used by image reading. Vision-reading 2026-10-27: Terra `low`
  and `off` 48/48, $0.005, 4 s median vs 6.1 Sol `low` 48/48, $0.0128, 9 s (ceiling; cost and
  latency decide). Owner decision 2026-10-27.
- `operator` → GPT-6.1 Terra : low. Used by the `operator` agent (browser and computer preludes).
  OMP has no browser/computer model role; the agent carries the route. Owner decision 2026-10-27,
  untested on operator work: OpenAI positions Terra for computer use at half 6.1 Sol's price.
- `innovation-council` → Opus 5.5 : high. Used by council lead. Owner: Opus-led council.
- `innovation-challenger` → GPT-6 Astra : high. Used by council and experiment-design peer.
  Cross-family challenger.
- `experiment-design` → Fable 5.1 : high. Used by experiment lead. Owner: Fable leads novel-problem
  framing.
- `experiment-peer` → Opus 5.5 : high. Used by experiment-design peer. Owner: Opus participates.

## Service tiers

`task.agentServiceTierOverrides`: `sonic` uses `priority` (Fast): 2.5× Codex
credits, measured ~20% lower wall time on the fixture set. `task` runs on
Sonnet at standard tier; it is already ~10× faster than 6.1 Sol, and Anthropic
priority billing on the subscription is unverified.

## Watch items

- Fable draws the shared Anthropic weekly limit faster than Opus and is capped
  at 50% of it; keep `experiment-design` for genuinely novel problems.
- GPT-5.5 retires from Codex on 2026-10-14; no route uses it.
- `task: sonnet-5-5:low` rests on 9 trials per arm across three hard fixtures,
  all arms at ceiling; rerun the hard set when the model changes or subagent
  implementation failures rise. Review, judging, vision and `operator` draw
  Codex (Team 7-day 62%, Pro 8% on 2026-09-30); implementation and `designer`
  draw Anthropic (14%). Watch both.
- Review-detection and vision-reading both hit their ceilings; judge, reviewer
  and vision routes are cost decisions among equals. Add harder cases before
  re-litigating them.
- Open-ended work handed to `task` without a plan under-delivers at low effort
  (a fixture-building task returned fixtures at a fifth of the requested size);
  the orchestrator should plan before delegating to `task`.
