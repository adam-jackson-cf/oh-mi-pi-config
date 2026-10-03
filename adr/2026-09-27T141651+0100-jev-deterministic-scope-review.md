# ADR: Automatic deterministic context for Jev scope review

- Date: 2026-09-27 14:16:51 +01:00
- Status: Accepted for shadow evaluation; routing and threshold require later evidence.
- Context: `agent/extensions/jev-watchdog.ts` and `agent/WATCHDOG.yml`.

## Problem

The passive Jev advisor previously sent its full system prompt, all earlier rendered advisor user
chunks, and the latest update to a broad five-dimension complexity question. Its `P(yes) >= 0.9`
response triggered a blocker and KISS review. That probability had not been calibrated against
labelled Jev cases. Large context, ambiguous criteria, and missing reviewer outcomes made false
positives and later evaluation hard to diagnose. Jev receives OMP-rendered transcript chunks, not
arbitrary repository files; tool results are usually summarized, with edit diffs sometimes expanded.
The primary agent did not select files for Jev.

The review discussion asked for a more targeted named template, a narrow question, reversible
routing, richer audit records, and explicit failures. The important constraint is to retain
immediate, automatic, passive coverage without an additional model-preparation step.

## Options considered

1. **Require the primary model to prepare and submit a Jev template.** It could identify relevant
   files, evidence, justification, and constraints. Rejected: a review would occur only if and when
   the model submitted it; an update might remain unreviewed indefinitely. It also adds another
   model-dependent action before the watchdog can act.
2. **Automatically review with deterministic context, then optionally let the model submit richer
   context for a second review.** Rejected after discussion: the second call would overlap the
   first, could happen a turn or much later, and might never happen. It would not enrich the first
   result retroactively.
3. **Automatically populate one bounded, named template from the native advisor update and call Jev
   once.** Chosen. It retains passive timing, produces comparable records, and makes missing or
   clipped evidence explicit. Deterministic extraction cannot establish that older constraints or
   unmentioned files are irrelevant; the reviewer must use `unknown` where that uncertainty matters.

## Decision

On each agent-containing advisor update, code extracts recent rendered user requests and the current
rendered agent activity into a versioned, bounded template. It sends only the configured Jev review
policy, not the entire advisor system prompt or all earlier chunks. Limits and omitted-history
markers are explicit. An update with no agent activity is not assessed. There is no model-submitted
second path.

Amendment (2026-09-28, policy `scope-drift-2026-09-28`): native advisor updates after the first
contain only new messages, so an evaluation of the first live session found 12 of 16 reviews without
a user objective. The extension now keeps, per advisor session, the latest observed task context and
constraints in a bounded in-memory map (32 sessions) and reuses them for updates that contain none,
marked `source: "carried_forward"`. Any update containing a user request replaces the carried value,
so a superseded objective is never reused. The earlier "no context cache" decision was based on a
single-update test and is withdrawn. Audit logs from earlier policies were deleted.

Amendment (2026-09-28, policy `proportionality-2026-09-28`): the review reference is now the user's
request plus the approved plan. The user approves plans and todo lists before the agent acts on
them, so their content is valid scope, not evidence to discount. The extension records the latest
plan file the agent read or wrote (`*plan*.md`, `local://*.md`, `.todo/**`) and the latest rendered
todo list per advisor session, re-reads the plan at each review (bounded to 4,000 characters,
redacted), and sends both as `approved_plan`. The question changed from scope drift to
proportionality: each change outside the approved scope must be required and the smallest
sufficient change; `unknown` is limited to a missing request or an invisible change so close calls
are decided. The `evaluate-jev` rubric applies the same tests.

Amendment (2026-09-28, policy `proportionality-implementation-2026-09-28`): `agent_activity` now
contains only implementation steps: `write` and `edit` tool calls with their rendered diffs and
results, excluding writes to plan files. Reading, searching, skill loading, todo updates, shell
commands, and prose are dropped, and an update without an implementation step is not sent to Jev.
A live run had shown non-trivial `P(yes)` on skill-file reads that the rubric excludes. Plan and
todo detection still scan the full activity.

The Jev question is limited to concrete scope drift: an added behavior or deliverable beyond the
user's objective and necessary supporting work. This deliberately does not replace the separate,
disabled multi-dimensional complexity judge or introduce completion-evidence triage. A Jev positive
is a shadow review candidate in the audit, not a blocker or an instruction to stop. The 0.9
threshold is retained solely as an experimental routing datum, not a calibrated accuracy statement.
OpenRouter returned HTTP 400 for both `~typesafe/jev-1.13.0` and `~typesafe/jev-1.13-20260917`,
while its `~typesafe/jev-latest` alias resolved to `typesafe/jev-1.13-20260917`. The request
therefore uses that alias but rejects a different resolved model before using its decision; an alias
move becomes an unresolved review rather than silent behavior drift. The resolved model and
question/policy version are recorded with the redacted populated template, probabilities, and
request digest. A human may append a linked outcome label through `/jev-label`; the provider
decision is never misrepresented as that independent label.

Provider errors, invalid decisions, rate limits, and audit failures are unresolved observations, not
negative judgments. The provider call has a 20-second timeout; native OMP advisor recovery bounds
consecutive failures and owns retries. One nonblocking concern per outage identifies human follow-up
ownership. Audit writes are observational and do not suppress a review. The audit path reported by
the mutable session manager may be stale under concurrent sessions; the response ID joins to the
actual native advisor trace. Audit data remains sensitive local data despite best-effort redaction.

## Consequences and reevaluation

A bounded template may lose older requirements or evidence; its omission flags are not proof that
the retained context is sufficient. Shadow mode avoids interrupting legitimate work but does not
itself bring candidates to a human's attention: evaluation requires deliberate audit review and
labels. The manual label path is not an automatic correctness oracle. The narrower question also
leaves project fit, reuse, speculative machinery, and simplest-sufficient-approach judgments outside
this Jev experiment.

Before changing the threshold, widening the question, or restoring blocking, compare same-version
Jev decisions on labelled cases, inspect false positives and false negatives by missing-context
status, and link subsequent human outcomes to request IDs. Repeat that evaluation when the model,
template, question, or routing policy changes. If important older constraints are systematically
omitted, improve deterministic extraction or reconsider model-prepared review explicitly rather than
silently appending a duplicate second call.

Initial, separate live comparison (2026-09-27): six synthetic labelled cases (two scope excess,
three in-scope, one underspecified) were sent to OpenRouter Jev `typesafe/jev-1.13-20260917` with
both the narrow question and a broad five-dimension question on the same targeted state. Both
selected the supplied label in all six; `P(yes)` differed slightly in several cases. This tiny
constructed set demonstrates route and question behavior, not superiority, calibration, or
production accuracy. Collect independently labelled real outcomes before changing routing.

Update 2026-10-02 (`proportionality-item-2026-10-02.1`): the review moved from a native advisor
(every update, main and subagent sessions, about 943 reviews on 2026-10-02) to an extension hook on
the main session's todo `done`, judging the work done for that one item against the item, the todo
list, the request and the approved plan. The bounded per-field template was replaced by one token
budget near Jev's capacity with priority-ordered cutting (nothing was cut on 180 real items), and
the three composed questions by one (`beyond_item`). WATCHDOG.yml no longer lists `jev-scope`.
Measured on the Fastino benchmark (E4, `jev-lab/fastino-bench/results/REPORT.md`): AUROC 0.779 on
169 labelled real items (11 overreach), no better than the clipped advisor state; the gains are
cost (one call per completed item), no clipping, and a usable 0.6–0.8 probability band. Precision
at 0.6 is 0.16 on real items, so the policy stays in `shadow` and the reevaluation rules above
still apply.

Update 2026-10-02 (`proportionality-item-2026-10-02.3`): code now decides before Jev. A shell
`git commit`/`push` that nothing in scope asks for flags at P(yes) = 1 without a call (`rule`);
items with no unmentioned path, system, git or dependency change are not sent (`router`, P = 0);
the rest ask one Choice, `worst_change`, with P(yes) = P(oversized) + P(outside), at 0.7/0.8.
Edits are shown as result diffs. Evidence: a replay of every main session (1,155 items), split by
session, 556 two-family blind labels (21 overreach); chosen on dev, scored once on test: AUROC
0.976 against 0.772 for `.1`, 6/7 caught with 4/157 false at 0.8, Jev called on ~2/3 of items
(`jev-lab/fastino-bench/results/REPORT.md` finding 9). Weak spot: oversized changes inside files
the request names (13/40 injected caught). Mode stays `shadow` until the owner decides on
`enforce`.

Reference:
`/Users/adamjackson/Projects/inflight/code-research/articles/2026-09-27-jev-effective-use.md`
(review guidance).
