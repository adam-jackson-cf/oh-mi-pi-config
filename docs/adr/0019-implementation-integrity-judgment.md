# 0019: Implementation integrity is a packaged judgment

Status: accepted, 2026-11-21. Supersedes in part
[ADR 0007](0007-delivery-principles-and-policy-homes.md) (the `main/implementation-integrity` tag).
Superseded in part by [ADR 0017](0017-dynaflows-runtime-rails.md#judged-rails): the judgment runs at
structured commits. Superseded in part by
[ADR 0021](0021-judgments-enforced-from-the-start.md): it is enforced there from the start; no
shadow or provisional status remains.

## Context

Six delivery-principles bullets cited `main/implementation-integrity`, a judge that did not exist.

## Decision

| Item | Value |
| --- | --- |
| Judgment | `agent/extensions/dynaflows/judgments/implementation-integrity/` |
| Question | `weakens_verification`: does a commit diff weaken how the repo verifies itself |
| Model | `typesafe/jev-1.14.0` |
| Status | Enforced at structured commits ([ADR 0021](0021-judgments-enforced-from-the-start.md); was provisional, shadow until `ready`) |
| Tag | `Judged by implementation-integrity/weakens_verification.` |

The policy test requires each such tag to name a decision in that judgment's `thresholds.json`.

## Consequences

The bullets name a real, measured judgment. ADR 0017 wires it into the commit rail. That it only
logged and noticed while provisional is superseded by
[ADR 0021](0021-judgments-enforced-from-the-start.md): it refuses.
