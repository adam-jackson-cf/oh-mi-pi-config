# Pricing and subscription quota

Prices move; re-read the sources on every review and update the dated figures.

## Sources

- Catalog API list prices: `omp models --json` (the snapshot script tabulates
  them). Treat as a relative guide to quota burn on subscriptions.
- Live quota: `omp usage --json --redact`; trend: `omp usage --history --days 14`.
- Actual spend by model: `omp stats --summary` (slow first run; it syncs sessions).
- Codex credit rates and Fast multiplier: <https://learn.chatgpt.com/docs/pricing>
  (Token rates table) and <https://learn.chatgpt.com/docs/agent-configuration/speed>.
- Anthropic Fable on plans:
  <https://support.claude.com/en/articles/15424964-claude-fable-models-on-your-plan>.
- Anthropic model launch pages for price and effort guidance, e.g.
  <https://www.anthropic.com/claude-sonnet-5-5>.

## Findings as of 2026-09-28

- Codex Standard credits per 1M tokens (input / cached / output): GPT-6 Astra
  250 / 25 / 1,250; GPT-6 Sol 50 / 5 / 250; GPT-6 Luna 2.5 / 0.25 / 12.5;
  GPT-5.6 Sol 100 / 10 / 500; GPT-5.6 Terra 50 / 5 / 300; GPT-5.6 Luna 5 / 0.5 /
  30. Luna costs 1/20 of Sol; GPT-5.6 Sol costs 2× GPT-6 Sol.
- Codex Fast mode costs 2.5× Standard credits for GPT-6 and GPT-5.6 models.
  Fast Luna still costs 1/8 of Standard Sol.
- Claude Sonnet 5.5 (2026-09-28): $2 / $10 per 1M input / output, cache read
  $0.20, cache write $2.50; half of Opus 5.5. Draws the shared Anthropic weekly
  limit. Claude Code 2.1.284 and OMP 18.3.2 both run it without upgrading.
- Measured per hard fixed-plan trial (2026-09-28): Sonnet 5.5 `low` $0.11,
  GPT-6 Luna `medium` 0.23 credits, GPT-6 Sol `high` 5.3 credits, GPT-5.6 Sol
  `low` 9.7 credits, GPT-5.6 Terra `xhigh` 9.5 credits.
- Codex published 5-hour local-message estimates (Plus / Pro 20×): Astra
  5–45 / 100–900, Sol 15–150 / 300–3,000, Luna 350–3,000 / 7,000–56,000.
- GPT-5.5 retires from Codex on 2026-10-14; remove it from routes and fallbacks.
- Anthropic Fable is **not** a separate quota. On Max it draws from the shared
  weekly limit, uses it faster than other Claude models, and is capped at 50%
  of that limit. `omp usage` shows it as `anthropic:7d:fable` (scope
  `tier: fable`) beside the shared `anthropic:7d` (scope `shared: true`).
- Observed load before this routing (2026-09-14 to 09-28): both Codex accounts
  hit 100% of their 7-day meter; Anthropic 7-day peaked at 12%.
- 2026-09-28 experiment runs: 36 Codex implementation trials moved the Codex
  Team 5-hour meter 0% → 42%; 27 Sonnet 5.5 trials moved the Anthropic 5-hour
  meter 2% → 3%. Moving `task` and `vision` to Sonnet 5.5 shifts load to the
  provider with headroom.
