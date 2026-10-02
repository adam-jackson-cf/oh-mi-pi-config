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
  process > Jev classifier > LLM**. Rules, parsers, codegraph, LSP, grep, git and
  tests first; a Jev decision (`find`, `jev_ask`, eval `judge()`, the Jev policy
  extensions) only for what rules cannot settle; an LLM or subagent only when
  Jev is unsure or the policy escalates.
- Jev policies ship in shadow mode (`agent/jev-policies.json`) and move to
  enforce only after a frozen experiment on human-labelled decisions from the
  Jev lab (`~/.omp/jev-lab`).
- Compaction stays on the native mechanism until an experiment shows a Jev
  boundary signal beats it.

## Fixed settings

- `modelRoles.advisor` stays `openai-codex/gpt-6-astra:low`. It belongs to the
  advisor watchdog setup that runs with the Jev classifier extension
  (`agent/extensions/jev-watchdog.ts`, `agent/WATCHDOG.yml`). Do not re-route
  it for cost or family reasons.

## Working agreements

- Explain proposals before executing config changes unless the owner asks
  for the change directly.
- Test claims about model behaviour with a frozen experiment before routing on
  them (see `experiments.md`).
- Commits and pushes need separate explicit permission.
