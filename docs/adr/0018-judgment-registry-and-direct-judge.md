# 0018: One judgment registry on the direct TypeSafe judge

Status: proposed, 2026-11-21 (owner to accept).

## Context

Two judgments loaded their own questions and thresholds with different version-key checks. The
judge role routed through OpenRouter with an OpenRouter fallback. `judge()` reports no model, so a
fallback answer used thresholds tuned on another model without any sign. Calls were not logged.

## Decision

| Area | Decision |
| --- | --- |
| Judge | `modelRoles.judge: typesafe/jev-1.14.0`, direct; no judge fallback chain |
| Registry | `load_judgment(name)` reads `judgments/<name>/`; stale keys raise |
| Model check | Judgments refuse when the configured judge role differs from `thresholds.model` |
| Outcomes | `decide(name, state)`: yes >= `yes_at_least`, no <= `no_at_most`, else escalate |
| Log | Every call appends to `<agent dir>/dynaflows/judgment-calls.jsonl`; rails read it in TS |
| Callers | Advisory triage and structure review use it; escalate never lowers or finds |
| Inspector | `inspector.env` sets `TYPESAFE_BASE_URL` to jev-bridge; gate judgments refuse it |
| omp | One version: devices must run the `package.json` pin |

## Consequences

Changing the judge model or any question makes every threshold stale until cases are re-scored and
re-tuned. A judge outage fails the judgment instead of falling back. A session started with the
Inspector route cannot run gate judgments.
