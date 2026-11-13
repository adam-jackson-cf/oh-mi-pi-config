# Current routing

Routing decision updated 2026-11-10 for the owner's Fable 5.2 experiment-design upgrade.
The installed source of truth is `~/.omp/agent/config.yml`; update this reference alongside
routing changes. Claude routes use
`anthropic`; GPT routes use `openai-codex`.

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
- `task` → GPT-6.1 Sol : medium. Implementation, including small fixes delegated by main.
  Earlier Sonnet `low` fixed-plan measurements do not validate this route.
- `smol` → GPT-6 Luna : low. Used by `sonic` (Fast) and prewalk.
  The `scout` agent is an exception: `task.agentModelOverrides.scout` explicitly selects
  `anthropic/claude-haiku-5-5:medium`, not `@smol`.
- `tiny`, `commit` → GPT-6 Luna : low. Used by titles, commit flow. Cheapest; replaced missing
  `gpt-5.4-mini`.
- `web` → GPT-6 Luna. Used by `web_search`. Verified working 2026-09-28.
- `kiss` → GPT-6.1 Sol : low. Cut acceptance criteria and open questions before a spike.
- `vision` → GPT-6.1 Terra : low. Used by image reading. Owner decision 2026-10-27.
- `operator` → GPT-6.1 Terra : off. Used by the `operator` agent (browser and computer preludes).
  OMP has no browser/computer model role; the agent carries the route. Terra `:off` supports
  answer-only output (see `omp-mechanics.md`); operator-task performance remains untested here.
- `innovation-council` → Opus 5.5 : high. Used by council lead. Owner decision 2026-10-30: the
  council is Opus 5.5 high (lead, and `experiment-peer` when it spawns one) plus GPT-6 Astra high
  (challenger); the lead had drifted to Fable 5.1 high in `config.yml` while this table said Opus.
- `innovation-challenger` → GPT-6 Astra : high. Used by council and experiment-design peer.
  Cross-family challenger.
- `experiment-design` → Fable 5.2 : high. Used by experiment lead. Owner: Fable leads novel-problem
  framing. Owner decision 2026-11-10; Fable 5.2 released 2026-11-09, $8/$40 per Mtok input/output
  versus Fable 5.1 $10/$50 per snapshot catalog.
- `experiment-peer` → Opus 5.5 : high. Used by experiment-design peer. Owner: Opus participates.

## Service tiers

`task.agentServiceTierOverrides` sets `priority` for exactly three agents:
`sonic`, `task` and `kiss`. Their routes are respectively GPT-6 Luna `low`,
GPT-6.1 Sol `medium` and GPT-6.1 Sol `low`. Other agents have no per-agent tier override.

## Watch items

- Fable draws the shared Anthropic weekly limit faster than Opus and is capped
  at 50% of it; keep `experiment-design` for genuinely novel problems.
- Implementation (`task`), `sonic`, `kiss`, vision and `operator` draw Codex;
  main/planning, `designer`, `reviewer`, `scout` and Anthropic council/experiment
  roles draw Anthropic. Monitor both quotas; old snapshots are not current capacity evidence.
- The earlier fixed-plan and review-detection experiments evaluated superseded routes.
  Do not use their ceiling scores to claim the current Sol `medium` implementation route
  was measured.
