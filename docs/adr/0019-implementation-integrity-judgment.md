# 0019: Implementation integrity is a packaged judgment

Status: accepted, 2026-11-21. Supersedes in part
[ADR 0007](0007-delivery-principles-and-policy-homes.md) (the `main/implementation-integrity` tag).

## Context

Six delivery-principles bullets cited `main/implementation-integrity`, a judge that did not exist.

## Decision

| Item | Value |
| --- | --- |
| Judgment | `agent/extensions/dynaflows/judgments/implementation-integrity/` |
| Question | `weakens_verification`: does a commit diff weaken how the repo verifies itself |
| Model | `typesafe/jev-1.14.0` |
| Status | provisional, shadow; not wired into rails |
| Tag | `Judged by implementation-integrity/weakens_verification.` |

The policy test requires each such tag to name a decision in that judgment's `thresholds.json`.

## Consequences

The bullets name a real, measured judgment. It gates nothing until a later decision wires it.
