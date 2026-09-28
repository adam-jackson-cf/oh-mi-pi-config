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

## Findings as of 2026-09-28

- Codex Standard credits per 1M tokens (input / cached / output): GPT-6 Astra
  250 / 25 / 1,250; GPT-6 Sol 50 / 5 / 250; GPT-6 Luna 2.5 / 0.25 / 12.5.
  Luna costs 1/20 of Sol.
- Codex Fast mode costs 2.5× Standard credits for GPT-6 Astra, Sol, and Luna.
  Fast Luna still costs 1/8 of Standard Sol.
- Codex published 5-hour local-message estimates (Plus / Pro 20×): Astra
  5–45 / 100–900, Sol 15–150 / 300–3,000, Luna 350–3,000 / 7,000–56,000.
- GPT-5.5 retires from Codex on 2026-10-14; remove it from routes and fallbacks.
- Anthropic Fable is **not** a separate quota. On Max it draws from the shared
  weekly limit, uses it faster than other Claude models, and is capped at 50%
  of that limit. `omp usage` shows it as `anthropic:7d:fable` (scope
  `tier: fable`) beside the shared `anthropic:7d` (scope `shared: true`).
- Observed load before this routing (2026-09-14 to 09-28): both Codex accounts
  hit 100% of their 7-day meter; Anthropic 7-day peaked at 12%.
