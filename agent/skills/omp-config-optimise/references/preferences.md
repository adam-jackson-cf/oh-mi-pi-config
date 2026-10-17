# Owner preferences

Standing decisions from the owner. Apply them without re-asking. Change them only
when the owner says so; evidence that argues against one goes to the owner as a
recommendation, not a silent edit.

## Objective

- Maximise use of the paid subscriptions (Anthropic Max, OpenAI Codex Pro and
  Team). Spread load so neither provider's quota runs out while the other sits
  idle. API list price is the tie-breaker, not the goal.

## Role placement

- Main orchestration, ideation, planning (`default`, `plan`, `slow`): Claude Opus (current: 5.5);
  reasoning ≤ medium.
- Design (`designer`): Claude Sonnet (current: Sonnet 5.5 `medium`, owner decision 2026-09-30);
  reasoning ≤ medium.
- Code review (`reviewer`): an independent session with none of the author's context; same family
  is fine; prefer GPT Sol (current: GPT-6.1 Sol `low`, owner decision 2026-09-30; independence rule
  owner decision 2026-10-02).
- Implementation with a fixed plan or todo list (`task`): Claude Sonnet (current: Sonnet 5.5, owner
  decision 2026-09-30 for Sonnet's ~10× shorter wall time over GPT-6.1 Sol and, at the time,
  split-family review, which is no longer required); reasoning the lowest effort the fixed-plan
  experiment shows sufficient (`low`).
- Image reading (`vision`) and browser/computer use (`operator` agent): GPT Sol (current: GPT-6.1
  Sol `low`, owner decision 2026-09-30).
- Simple, low-reasoning actions (`smol`, `tiny`, `commit`, `sonic`, `scout`): GPT Luna; reasoning
  lowest effort.
- Judging plans and output (`plan-judge`, `test-judge`, `completionist`, `kiss`): an independent
  session; prefer GPT Sol, same family as the author allowed (current: GPT-6.1 Sol `low`, owner
  decision 2026-09-30); reasoning as needed.
- Innovation council: Opus leads; GPT Astra challenges; reasoning high allowed.
- Experiment design: novel problems, hypothesis forming: Claude Fable leads; Astra and Opus as
  peers; reasoning high allowed.

## Reasoning caps

- Opus and Astra run at **medium or below** everywhere except the innovation
  council and experiment design. Planning is not an exception: when a plan
  needs rigorous input, route it to `innovation-council`.
- Above-medium reasoning outside those two is a deliberate, per-turn user
  choice for a distinctly unique problem, not a configured default.
- Work that arrives with a fixed plan or todo list does not need reasoning.

## Evidence ladder

- Decide with the cheapest mechanism that settles the question: **deterministic
  process > OMP's native Jev judgments > LLM**. Rules, parsers, codegraph, LSP,
  grep, git and tests first; native `find` or eval `judge()` only for what rules
  cannot settle; an LLM or subagent only when those are unsure.
- Compaction stays on the native mechanism until an experiment shows a better
  boundary signal.

## Removed setups

- No advisor or proportionality watchdog (owner decision 2026-10-03): the Jev
  scope watchdog, `WATCHDOG.yml`, `modelRoles.advisor`, `advisor.*` and
  `task.agentAdvisor` were removed. Do not reintroduce them in routing reviews.
- No Jev policy extensions (owner decision 2026-10-17): `jev-guard`,
  `jev-ask`/`jev_ask`, `agent/jev-policies.json`, the integrity rules and their
  weekly maintainer job, the Jev lab and the `evaluate-jev` skill were removed.
  Do not reintroduce them in routing reviews.

## Working agreements

- Explain proposals before executing config changes unless the owner asks
  for the change directly.
- Test claims about model behaviour with a frozen experiment before routing on
  them (see `experiments.md`).
- Commits and pushes need separate explicit permission.
