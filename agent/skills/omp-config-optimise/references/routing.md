# Current routing

Routing decision updated 2026-11-22: `reviewer` → GPT-6.1 Sol `medium`, `task` → Haiku 5.5 `high`,
`innovation-council` → Fable 5.2 `high` (owner decisions).
The installed source of truth is `~/.omp/agent/config.yml`; update this reference alongside
routing changes. Claude routes use `anthropic`; GPT routes use `openai-codex`; Gemini routes use
`agy`, the `agent/extensions/agy-cli.ts` provider that runs Google's Antigravity CLI.

## Roles

- `default` + `defaultThinkingLevel: medium` → Opus 5.5 : medium. Used by main session.
  Orchestrator; `auto` cannot be capped at medium.
- `plan` → Opus 5.5 : medium. Used by plan mode. Preference; rigorous plans go to the council.
- `slow` → Opus 5.5 : medium. Used by model cycling. Preference cap.
- `designer` → Sonnet 5.5 : medium. Used by `designer` agent. Owner decision 2026-09-30; no design
  experiment exists.
- `reviewer` → GPT-6.1 Sol : medium. Used by bundled `reviewer` through
  `task.agentModelOverrides.reviewer: "@reviewer"`; without the override it inherited the parent's
  model (fresh-process check 2026-09-28). Owner decision 2026-11-22 after two pre-registered runs:
  seeded defects (`~/omp-experiments/review-detection-2026-11-22/`) hit the ceiling; real defects from
  this repo's history (`~/omp-experiments/review-real-defects-2026-11-22/`, 15 cases, 26 defects) gave
  Neon − Sol +0.053 per-case detection, 95% CI [−0.035, +0.142]: no measured difference. The tie
  went to Sol by owner preference: native provider, no `agy` extension, no Google prompt-policy
  refusals.
- `task` → Haiku 5.5 : high. Implementation, including small fixes delegated by main. Owner
  decision 2026-11-22; no implementation experiment has measured this route.
- `smol` → GPT-6 Luna : low. Used by `sonic` (Fast) and prewalk.
  The `scout` agent is an exception: `task.agentModelOverrides.scout` explicitly selects
  `anthropic/claude-haiku-5-5:medium`, not `@smol`.
- `tiny`, `commit` → GPT-6 Luna : low. Used by titles, commit flow. Cheapest.
- `web` → GPT-6 Luna. Used by `web_search`. Verified working 2026-09-28.
- `kiss` → GPT-6.1 Sol : low. Cut acceptance criteria and open questions before a spike.
- `vision` → GPT-6.1 Terra : low. Used by image reading. Owner decision 2026-10-27.
- `operator` → GPT-6.1 Terra : off. Used by the `operator` agent (browser and computer preludes).
  OMP has no browser/computer model role; the agent carries the route. Terra `:off` supports
  answer-only output (see `omp-mechanics.md`); operator-task performance remains untested here.
- `innovation-council` → Fable 5.2 : high. Used by council lead. Owner decision 2026-11-22 (was
  Opus 5.5 high); `experiment-peer` stays Opus 5.5 high, GPT-6 Astra high challenges.
- `innovation-challenger` → GPT-6 Astra : high. Used by council and experiment-design peer.
  Cross-family challenger.
- `innovation-gemini-challenger` → Gemini 4 Neon : high via `agy`. Used by council only; agy runs
  it read-only in `plan` mode because the agent has no edit or shell tools. Owner decision
  2026-11-21.
- `experiment-design` → Fable 5.2 : high. Used by experiment lead. Owner: Fable leads novel-problem
  framing. Owner decision 2026-11-10; Fable 5.2 released 2026-11-09, $8/$40 per Mtok input/output.
- `experiment-peer` → Opus 5.5 : high. Used by experiment-design peer. Owner: Opus participates.

## Service tiers

`task.agentServiceTierOverrides` sets `priority` for exactly three agents:
`sonic`, `task` and `kiss`. Their routes are respectively GPT-6 Luna `low`,
Haiku 5.5 `high` and GPT-6.1 Sol `low`. Whether Anthropic honours `priority` for the `task`
route is unverified. Other agents have no per-agent tier override.

## Watch items

- Fable draws the shared Anthropic weekly limit faster than Opus and is capped
  at 50% of it; keep `experiment-design` for genuinely novel problems.
- `sonic`, `kiss`, `reviewer`, vision and `operator` draw Codex; the Neon challenger draws the
  Google AI subscription through `agy`; main/planning, `designer`, `task`, `scout` and Anthropic
  council/experiment roles draw Anthropic (Fable council lead draws the Fable cap). Monitor each
  quota; old snapshots are not current capacity evidence.
- The earlier fixed-plan and review-detection experiments evaluated superseded routes.
  Do not use their ceiling scores to claim the current Haiku `high` implementation route
  was measured.
- OMP's built-in `antigravity-cli` provider is not used: Google answers 429 `RESOURCE_EXHAUSTED`
  to specific lines of omp's system prompt sent through it, while `agy -p` with the same
  account succeeds (checked 2026-11-21).
