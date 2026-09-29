# Current routing

Last reviewed 2026-09-28. Source of truth is `~/.omp/agent/config.yml`; this
list records why. Update both together.

## Roles

- `default` + `defaultThinkingLevel: medium` → Opus 5.5 : medium. Used by main session.
  Orchestrator; `auto` cannot be capped at medium.
- `plan` → Opus 5.5 : medium. Used by plan mode. Preference; rigorous plans go to the council.
- `slow` → Opus 5.5 : medium. Used by model cycling. Preference cap.
- `designer` → Opus 5.5 : medium. Used by `designer` agent. Preference.
- `reviewer` → GPT-6 Sol : medium. Used by bundled `reviewer` through
  `task.agentModelOverrides.reviewer: "@reviewer"`; without the override it inherited the parent's
  Opus (fresh-process check 2026-09-28). Leaves the Claude family because `task` runs on Sonnet;
  review-detection 2026-09-28: every arm 36/36, cheapest qualifier (Opus also 36/36).
- `task` → Sonnet 5.5 : low, standard tier. Used by bundled `task`. Hard fixed-plan set 2026-09-28:
  Sonnet `low`/`medium`/`high` 9/9, GPT-6 Luna `medium` 6/9 (15/18 pooled); Sonnet ~5× faster.
  Moves implementation load to the Anthropic quota.
- `smol` → GPT-6 Luna : low. Used by `sonic` (Fast), `scout`, `lsp-evidence`, prewalk. Lowest
  effort; mechanical work.
- `tiny`, `commit` → GPT-6 Luna : low. Used by titles, commit flow. Cheapest; replaced missing
  `gpt-5.4-mini`.
- `web` → GPT-6 Luna. Used by `web_search`. Verified working 2026-09-28.
- `plan-judge`, `test-judge`, `completionist` → GPT-6 Sol : medium. Used by judges. Other family
  from the Opus and Sonnet authors. Review-detection 2026-09-28: GPT-6 Sol `high`/`medium`,
  GPT-5.6 Sol `low`, Terra `xhigh`, Astra `medium` all 36/36; `medium` cheapest (1.7 credits per
  review vs 4.9–10.6 for the GPT-5.6 and Astra arms).
- `kiss` → GPT-6 Sol : medium. Used by `kiss` agent. Other family from the Opus planner.
- `vision` → Sonnet 5.5 : low. Used by image reading. Vision-reading 2026-09-28: 48/48 vs GPT-6
  Sol `medium` 45/48; Anthropic quota.
- `advisor` → GPT-6 Astra : low. Used by advisor watchdog runtime alongside the Jev classifier
  extension. Owner-fixed; not a routing lever. Leave unchanged in reviews.
- `innovation-council` → Opus 5.5 : high. Used by council lead. Owner: Opus-led council.
- `innovation-challenger` → GPT-6 Astra : high. Used by council and experiment-design peer.
  Cross-family challenger.
- `experiment-design` → Fable 5.1 : high. Used by experiment lead. Owner: Fable leads novel-problem
  framing.
- `experiment-peer` → Opus 5.5 : high. Used by experiment-design peer. Owner: Opus participates.

## Service tiers

`task.agentServiceTierOverrides`: `sonic` uses `priority` (Fast): 2.5× Codex
credits, measured ~20% lower wall time on the fixture set. `task` runs on
Sonnet at standard tier; it is already ~5× faster than Fast Luna, and Anthropic
priority billing on the subscription is unverified.

## Jev policies

Extensions in `agent/extensions/` follow the evidence ladder (deterministic >
Jev > LLM). Modes live in `agent/jev-policies.json`; decisions go to
`agent/jev-audit/<policy>/` and are labelled in the Jev lab (`~/.omp/jev-lab`).

- `jev-guard.ts`: `guard.bash` (read-only allowlist, destructive and
  secret-read denylist, then Jev effect/intent/exposure), `guard.write`
  (secret paths and literals, then Jev `contains_secret` on credential-like
  assignments), `guard.result` (Jev `prompt_injection` on web, MCP and
  network-fetch output only). All `shadow`.
- `jev-subagent-policy.ts`: `subagent.review-triage` (sensitive/docs-only path
  rules, then Jev risk scores → light Luna / standard / deep Sol:high reviewer,
  never the author's family) and `subagent.effort` (explicit short plan keeps
  `low`; Jev openness can raise `task` to `medium`). Both `shadow`.
- `jev-ask.ts`: `jev_ask` tool (judge files or captured output without reading
  them into context) plus the evidence-ladder system-prompt nudge. Always on;
  every call is audited under `ask`.
- Promotion to `enforce`: ≥ 30 labelled decisions per policy (≥ 5 of the
  minority label), a frozen threshold chosen on the labelled set, and a
  regression check on the matching harness (`review-detection` for triage,
  `fixed-plan-effort` for effort).

## Watch items

- Fable draws the shared Anthropic weekly limit faster than Opus and is capped
  at 50% of it; keep `experiment-design` for genuinely novel problems.
- GPT-5.5 retires from Codex on 2026-10-14; no route uses it.
- `task: sonnet-5-5:low` rests on 9 trials per arm across three hard fixtures,
  all arms at ceiling; rerun the hard set when the model changes or subagent
  implementation failures rise. Watch the Anthropic 7-day meter now that
  implementation and vision draw it.
- Review-detection and vision-reading both hit their ceilings; judge, reviewer
  and vision routes are cost decisions among equals. Add harder cases before
  re-litigating them.
- Open-ended work handed to `task` without a plan under-delivers at low effort
  (a fixture-building task returned fixtures at a fifth of the requested size);
  the orchestrator should plan before delegating to `task`.
