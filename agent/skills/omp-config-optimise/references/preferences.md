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
- Verification (`verifier`): Claude Sonnet 5.5 `medium`, independent and cross-family to
  every author (Sol implementation by default), enforced by agent-rails with dynaflows on (2026-11-04).
- Bundled `reviewer`: Sonnet 5.5 `medium` for open-ended contextual eval judging, not the
  proof-first verification stage; preserve independent cross-family sessions.
- Implementation with a fixed plan or todo list (`task`): GPT-6.1 Sol `medium` with Codex
  `priority` service tier (owner decision 2026-11-03).
- Image reading (`vision`): GPT Terra (current: GPT-6.1 Terra `low`, owner decision 2026-10-27).
- Browser/computer use (`operator`): GPT-6.1 Terra `off` (owner decision 2026-11-03);
  operator-task performance remains untested here.
- Simple, low-reasoning actions (`smol`, `tiny`, `commit`, `sonic`): GPT Luna; reasoning
  lowest effort.
- Codebase discovery (`scout`): Claude Haiku 5.5 `medium` (owner decision 2026-11-03).
- Scope cutting (`kiss`): GPT-6.1 Sol `low`; cut acceptance criteria and open questions
  before a spike, not a risk review.
- Former plan/test/completeness judging roles: removed 2026-11-04, proof-first workflow.
- Codex `priority` service tier applies to exactly `task`, `kiss` and `sonic`
  (owner decision 2026-11-04).
- Innovation council: Opus leads; GPT Astra challenges; reasoning high allowed.
- Experiment design: novel problems, hypothesis forming: Claude Fable leads; Astra and Opus as
  peers; reasoning high allowed.

## Reasoning caps

- Opus and Astra run at **medium or below** everywhere except the innovation
  council and experiment design. Planning is not an exception: when a plan
  needs rigorous input, route it to `innovation-council`.
- Above-medium reasoning outside those two is a deliberate, per-turn user
  choice for a distinctly unique problem, not a configured default.
- Fixed-plan implementation uses the configured `task` route at `medium` (owner decision
  2026-11-03); historical lower-effort trials do not override the current route.

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
- Agents may create `agent/*` branches, commit, push, open PRs and rebase-merge them per
  `skill://git-tool` unless `.omp/gate.json` sets `agentMerge: disabled`; never commit to main
  (owner decision 2026-11-03).
