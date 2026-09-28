---
name: "evaluate-jev"
description:
  "USE WHEN reviewing Jev watchdog decision logs or assessing how the Jev scope-review advisor
  performed."
---

# Evaluate Jev Decisions

## Scope

- Read-only over `~/.omp/agent/sessions/**/jev-watchdog-requests.jsonl` and, when needed, the linked
  session transcripts. Never print credentials, API keys, or raw provider response bodies.
- Do not change Jev policy, thresholds, or routing during evaluation; report recommendations only.
- Only record human labels (`/jev-label`) when the user asks.

## Procedure

1. Summarize the logs deterministically:
   `bun ~/.omp/agent/skills/evaluate-jev/scripts/summarize.ts --cases /tmp/jev-cases.jsonl` Add
   `--policy <policy_version>` to restrict to the current policy (read it from
   `agent/extensions/jev-watchdog.ts`) and `--since <ISO timestamp>` for a window. Treat unversioned
   records as a legacy question; report them separately.
2. Check operational health first (rubric R5). If input sufficiency is below 80%, lead with that;
   accuracy numbers on missing-context cases say nothing about Jev.
3. Score every current-policy case with `references/rubric.md` (R1–R4). For more than ~30 cases,
   delegate scoring in batches to subagents, giving each the rubric file path and a case slice;
   require the per-case evidence citation. Spot-check disagreements yourself.
4. For any `yes`, `reviewCandidate`, `P(yes) ≥ 0.5`, or R1/R2 disagreement, open the linked session
   transcript to confirm the label.
5. Report:
   - Window, policy version, resolved model, and case counts.
   - The R3 confusion counts with denominators and request IDs for every error.
   - Template defects (R1 < 2 patterns and their likely cause).
   - Whether evidence supports any change; apply the rubric's sample-size caveat.

## Notes

- Prior human labels (`reviewer_outcome` records) override your R2 label; report disagreements
  rather than silently replacing either.
- Results feed the reevaluation criteria in
  `~/.omp/adr/2026-09-27T141651+0100-jev-deterministic-scope-review.md`.
