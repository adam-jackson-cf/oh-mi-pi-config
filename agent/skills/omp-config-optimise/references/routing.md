# Current routing

Routing decision updated 2026-11-04 for the proof-first workflow. The installed source of truth is
`~/.omp/agent/config.yml`; update this reference alongside routing changes. Claude routes use
`anthropic`; GPT routes use `openai-codex`. Historical experiments below are not fresh verification
of the current routes.

## Roles

- `default` + `defaultThinkingLevel: medium` → Opus 5.5 : medium. Used by main session.
  Orchestrator; `auto` cannot be capped at medium.
- `plan` → Opus 5.5 : medium. Used by plan mode. Preference; rigorous plans go to the council.
- `slow` → Opus 5.5 : medium. Used by model cycling. Preference cap.
- `designer`: removed 2026-11-04, owner decision.
- `reviewer` → Sonnet 5.5 : medium. Used by bundled `reviewer` through
  `task.agentModelOverrides.reviewer: "@reviewer"`; without the override it inherited the parent's
  model (fresh-process check 2026-09-28). Used for open-ended contextual eval judging,
  in an independent cross-family session, not the proof-first verification stage.
- `verifier` → Sonnet 5.5 : medium. Independent live proof verification, cross-family to every
  author (Sol by default); skill-gate enforces the family invariant (2026-11-04).
- `task` → GPT-6.1 Sol : medium. Implementation, including small fixes delegated by main.
  Earlier Sonnet `low` fixed-plan measurements do not validate this route.
- `smol` → GPT-6 Luna : low. Used by `sonic` (Fast), `lsp-evidence` and prewalk.
  The `scout` agent is an exception: `task.agentModelOverrides.scout` explicitly selects
  `anthropic/claude-haiku-5-5:medium`, not `@smol`.
- `tiny`, `commit` → GPT-6 Luna : low. Used by titles, commit flow. Cheapest; replaced missing
  `gpt-5.4-mini`.
- `web` → GPT-6 Luna. Used by `web_search`. Verified working 2026-09-28.
- `kiss` → GPT-6.1 Sol : low. Cut acceptance criteria and open questions before a spike.
- Former plan/test/completeness judging roles: removed 2026-11-04, proof-first workflow.
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

`task.agentServiceTierOverrides` sets `priority` for exactly three agents:
`sonic`, `task` and `kiss`. Their routes are respectively GPT-6 Luna `low`,
GPT-6.1 Sol `medium` and GPT-6.1 Sol `low`. Other agents have no per-agent tier override.

The historical `sonic` fixture measurement (~20% lower wall time at 2.5× Codex credits)
does not establish a speedup or cost multiplier for the other priority agents.

## Watch items

- Fable draws the shared Anthropic weekly limit faster than Opus and is capped
  at 50% of it; keep `experiment-design` for genuinely novel problems.
- Implementation (`task`), `sonic`, `kiss`, vision and `operator` draw Codex;
  main/planning, `reviewer`, `verifier`, `scout` and Anthropic council/experiment
  roles draw Anthropic. Monitor both quotas; old snapshots are not current capacity evidence.
- The earlier fixed-plan and review-detection experiments evaluated superseded routes.
  Do not use their ceiling scores to claim the current Sol `medium` implementation or
  Sonnet `medium` verification routes were measured.
- Vision-reading hit its ceiling; add harder cases before re-litigating that route.
- Independent sessions alone do not satisfy cross-family verification: a verifier must differ
  from every author family, including when overriding author or verifier routes.
