# Current routing

Routing decision updated 2026-11-21 for the owner's Google AI subscription routes (Gemini 4
Carbon implementation, Gemini 4 Neon council challenger).
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
- `reviewer` → Sonnet 5.5 : medium. Used by bundled `reviewer` through
  `task.agentModelOverrides.reviewer: "@reviewer"`; without the override it inherited the parent's
  model (fresh-process check 2026-09-28). Used for open-ended contextual eval judging,
  in an independent cross-family session, not dynaflows proof.
- `task` → Gemini 4 Carbon : medium via `agy`. Implementation, including small fixes delegated
  by main. Owner decision 2026-11-21. agy runs its own tool loop: its edits and commands bypass
  omp's rails and `gitGuard`, and omp skills, rules and context files are not forwarded.
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
- `innovation-council` → Opus 5.5 : high. Used by council lead. Owner decision 2026-10-30: the
  council is Opus 5.5 high (lead, and `experiment-peer` when it spawns one) plus GPT-6 Astra high
  (challenger).
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
Gemini 4 Carbon `medium` via `agy` and GPT-6.1 Sol `low`. The `agy` provider has no service
tier, so the `task` entry has no effect while `task` routes to `agy`. Other agents have no
per-agent tier override.

## Watch items

- Fable draws the shared Anthropic weekly limit faster than Opus and is capped
  at 50% of it; keep `experiment-design` for genuinely novel problems.
- `sonic`, `kiss`, vision and `operator` draw Codex; implementation (`task`) and the Neon
  challenger draw the Google AI subscription through `agy`; main/planning, `designer`,
  `reviewer`, `scout` and Anthropic council/experiment roles draw Anthropic. Monitor each
  quota; old snapshots are not current capacity evidence.
- The earlier fixed-plan and review-detection experiments evaluated superseded routes.
  Do not use their ceiling scores to claim the current Carbon `medium` implementation route
  was measured.
- OMP's built-in `antigravity-cli` provider is not used: Google answers 429 `RESOURCE_EXHAUSTED`
  to specific lines of omp's system prompt sent through it, while `agy -p` with the same
  account succeeds (checked 2026-11-21).
