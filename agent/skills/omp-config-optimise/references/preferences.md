# Owner preferences

Standing decisions from the owner. Apply them without re-asking. Change them only
when the owner says so; evidence that argues against one goes to the owner as a
recommendation, not a silent edit.

## Objective

- Maximise use of the paid subscriptions (Anthropic Max, OpenAI Codex Pro and
  Team, Google AI). Spread load so no provider's quota runs out while another sits
  idle. API list price is the tie-breaker, not the goal.

## Role placement

- Main orchestration, ideation, planning (`default`, `plan`, `slow`): Claude Opus (current: 5.5);
  reasoning ≤ medium.
- Design (`designer`): Claude Sonnet (current: Sonnet 5.5 `medium`, owner decision 2026-09-30);
  reasoning ≤ medium.
- Bundled `reviewer`: GPT-6.1 Sol `medium` (owner decision 2026-11-22: no measured detection
  difference from Gemini 4 Neon on real defects; prefer a native provider over `agy`).
- Implementation with a fixed plan or todo list (`task`): Claude Haiku 5.5 `high` (owner decision
  2026-11-22).
- Image reading (`vision`): GPT Terra (current: GPT-6.1 Terra `low`, owner decision 2026-10-27).
- Browser/computer use (`operator`): GPT-6.1 Terra `off` (owner decision 2026-11-03);
  operator-task performance remains untested here.
- Simple, low-reasoning actions (`smol`, `tiny`, `commit`, `sonic`): GPT Luna; reasoning
  lowest effort.
- Codebase discovery (`scout`): Claude Haiku 5.5 `medium` (owner decision 2026-11-03).
- Scope cutting (`kiss`): GPT-6.1 Sol `low`; cut acceptance criteria and open questions
  before a spike, not a risk review.
- Codex `priority` service tier applies to exactly `task`, `kiss` and `sonic`
  (owner decision 2026-11-04).
- Innovation council: Fable 5.2 leads (owner decision 2026-11-22); GPT Astra and Gemini 4 Neon
  (via `agy`) challenge; Opus 5.5 is `experiment-peer`; reasoning high allowed.
- Experiment design: novel problems, hypothesis forming: Claude Fable leads; Astra and Opus as
  peers; reasoning high allowed.

## Reasoning caps

- Opus and Astra run at **medium or below** everywhere except the innovation
  council and experiment design. Planning is not an exception: when a plan
  needs rigorous input, route it to `innovation-council`.
- Above-medium reasoning outside those two is a deliberate, per-turn user
  choice for a distinctly unique problem, not a configured default.
- Fixed-plan implementation uses the configured `task` route, Haiku 5.5 `high` (owner decision
  2026-11-22).

## Evidence ladder

- Decide with the cheapest mechanism that settles the question: **deterministic
  process > OMP's native Jev judgments > LLM**. Rules, parsers, LSP, ast-grep,
  grep, git and tests first; native `find` or eval `judge()` only for what rules
  cannot settle; an LLM or subagent only when those are unsure.
- Compaction stays on the native mechanism until an experiment shows a better
  boundary signal.

## Working agreements

- Explain proposals before executing config changes unless the owner asks
  for the change directly.
- Test claims about model behaviour with a frozen experiment before routing on
  them (see `experiments.md`).
