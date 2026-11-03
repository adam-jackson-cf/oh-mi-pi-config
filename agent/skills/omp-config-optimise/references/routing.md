# Current routing

Last reconciled with `agent/config.yml` on 2026-11-03. The installed source of truth is
`~/.omp/agent/config.yml`; update this reference alongside routing changes. Claude routes use
`anthropic`; GPT routes use `openai-codex`. Historical experiments below are not fresh verification
of the current routes.

## Roles

- `default` + `defaultThinkingLevel: medium` → Opus 5.5 : medium. Used by main session.
  Orchestrator; `auto` cannot be capped at medium.
- `plan` → Opus 5.5 : medium. Used by plan mode. Preference; rigorous plans go to the council.
- `slow` → Opus 5.5 : medium. Used by model cycling. Preference cap.
- `designer` → Sonnet 5.5 : medium. Used by `designer` agent. Owner decision 2026-09-30; no design
  experiment exists.
- `reviewer` → Sonnet 5.5 : medium. Used by bundled `reviewer` through
  `task.agentModelOverrides.reviewer: "@reviewer"`; without the override it inherited the parent's
  model (fresh-process check 2026-09-28). Reviews run in independent sessions and must use a
  different model family from the author: Sonnet reviews GPT-6.1 Sol implementation.
- `task` → GPT-6.1 Sol : medium. Used by bundled `task` for implementation, including small
  fixes delegated by the Opus main session. This keeps implementation and Sonnet review
  cross-family; earlier Sonnet `low` fixed-plan measurements do not validate this route.
- `smol` → GPT-6 Luna : low. Used by `sonic` (Fast), `lsp-evidence` and prewalk.
  The `scout` agent is an exception: `task.agentModelOverrides.scout` explicitly selects
  `anthropic/claude-haiku-5-5:medium`, not `@smol`.
- `tiny`, `commit` → GPT-6 Luna : low. Used by titles, commit flow. Cheapest; replaced missing
  `gpt-5.4-mini`.
- `web` → GPT-6 Luna. Used by `web_search`. Verified working 2026-09-28.
- `plan-judge`, `kiss` → GPT-6.1 Sol : low. Independent cross-family judges of Opus plans.
- `test-judge`, `completionist` → Sonnet 5.5 : medium. Independent cross-family judges of
  GPT-6.1 Sol implementation and its tests/completeness.
- `vision` → GPT-6.1 Terra : low. Used by image reading. Vision-reading 2026-10-27: Terra `low`
  and `off` 48/48, $0.005, 4 s median vs 6.1 Sol `low` 48/48, $0.0128, 9 s (ceiling; cost and
  latency decide). Owner decision 2026-10-27.
- `operator` → GPT-6.1 Terra : off. Used by the `operator` agent (browser and computer preludes).
  OMP has no browser/computer model role; the agent carries the route. Terra `:off` supports
  answer-only output (see `omp-mechanics.md`); operator-task performance remains untested here.
- `innovation-council` → Opus 5.5 : high. Used by council lead. Owner decision 2026-10-30: the
  council is Opus 5.5 high (lead, and `experiment-peer` when it spawns one) plus GPT-6 Astra high
  (challenger); the lead had drifted to Fable 5.1 high in `config.yml` while this table said Opus.
- `innovation-challenger` → GPT-6 Astra : high. Used by council and experiment-design peer.
  Cross-family challenger.
- `experiment-design` → Fable 5.1 : high. Used by experiment lead. Owner: Fable leads novel-problem
  framing.
- `experiment-peer` → Opus 5.5 : high. Used by experiment-design peer. Owner: Opus participates.

## Service tiers

`task.agentServiceTierOverrides` explicitly sets `priority` for exactly four agents:
`sonic`, `task`, `plan-judge` and `kiss`. Their routes are respectively GPT-6 Luna `low`,
GPT-6.1 Sol `medium`, GPT-6.1 Sol `low` and GPT-6.1 Sol `low`. Other agents have no
per-agent service-tier override in the current config.

The historical `sonic` fixture measurement (~20% lower wall time at 2.5× Codex credits)
does not establish a speedup or cost multiplier for the other priority agents.

## Watch items

- Fable draws the shared Anthropic weekly limit faster than Opus and is capped
  at 50% of it; keep `experiment-design` for genuinely novel problems.
- Implementation (`task`), `sonic`, `plan-judge`, `kiss`, vision and `operator` draw Codex;
  main/planning, `designer`, `reviewer`, `test-judge`, `completionist`, `scout` and the
  Anthropic council/experiment roles draw Anthropic. Monitor both quotas; old quota snapshots
  are not current capacity evidence.
- The earlier fixed-plan and review-detection experiments evaluated superseded routes.
  Do not use their ceiling scores to claim the current Sol `medium` implementation or
  Sonnet `medium` review/judging routes were measured.
- Vision-reading hit its ceiling; add harder cases before re-litigating that route.
- Independent sessions alone do not satisfy the workflow's cross-family review requirement.
  If overriding an author or judge model, preserve both session isolation and different families.
