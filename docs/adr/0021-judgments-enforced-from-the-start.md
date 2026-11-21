# 0021: Every judgment is enforced from the start

Status: accepted, 2026-11-21 (owner ruling). Supersedes in part
[ADR 0003](0003-structure-checks-at-pr-diff-scope.md), [ADR 0005](0005-comment-ban-and-integrity-blockers.md)
and [ADR 0007](0007-delivery-principles-and-policy-homes.md) (shadow-only structure findings),
[ADR 0017](0017-dynaflows-runtime-rails.md#judged-rails) (shadow status and readiness),
[ADR 0019](0019-implementation-integrity-judgment.md) (provisional status) and
[ADR 0020](0020-judgment-registry-and-direct-judge.md) (escalate bands).

## Context

Each Jev judgment carried a `provisional` or `ready` status. Rails and `structure_review()` ran
every provisional judgment in shadow: logged and noticed, never acted on. No judgment was ever
`ready`, so no judged rail acted. Bands escalated the uncertain middle, but no fallback judge
existed to answer it.

## Decision

| Area | Decision |
| --- | --- |
| Status | Gone. A `thresholds.json` or decision with `status` is refused |
| Rails | Act on every verdict of every registered judgment from the moment it ships |
| Judge failure | Unchanged from the enforced path: refuse commit, merge, command; evidence unmet |
| Decision | A single cutoff `{"threshold": t}`: yes when score >= t, else no |
| Band | `no_at_most`, `yes_at_least`, `fallback`: valid only with a registered fallback judgment |
| Shipped bands | Each became a threshold at its former `yes_at_least`, pending retune |
| `reached` | Its `no` lowers severity, so its threshold is the former `no_at_most` (0.3) |
| Structure review | Every finding sets `halt`; result status is `pass`, `incomplete` or `blocked` |
| Labels | The innovation council labels first; the arbiter decides; the owner may override |

## Consequences

Shipping a judgment now changes agent behaviour at once, so its thresholds must be fit to act on
when they merge. Without bands no shipped judgment escalates, so owner `$approve` prompts follow
only refusals the owner chooses to override. A rail that acts on `no` (in-scope asks refused,
extension-impact review skipped) now reads every score below the threshold as `no`. Thresholds
stay untuned where labels are few; each `thresholds.json` lists that shortfall.
