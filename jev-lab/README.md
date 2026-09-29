# Jev lab

Two things live here:

- **Upstream demo lab**: the lab shown in the "10 Levels of Jev" video, cloned at a pinned commit
  into `upstream/` (runtime-only, gitignored). It is a demo; nothing you label there is kept.
- **Workbench** (`server.ts`, `lib/`, `public/`): the local labelling and evaluation tool for every
  Jev use in this repo. **This is where labels live.** Original code, no build step.

## Start

```sh
bun ~/.omp/jev-lab/server.ts            # workbench, http://127.0.0.1:4398
~/.omp/jev-lab/scripts/upstream-lab.sh setup   # once: clone + build the upstream demo
~/.omp/jev-lab/scripts/upstream-lab.sh run     # upstream demo, http://localhost:4399
```

The workbench key is `OPENROUTER_API_KEY` if set, otherwise the stdout of `omp token openrouter`
captured at startup. Without a key, Replay, Playground and case-set runs are disabled (visible
notice); labelling and metrics still work.

## Labelling workflow: 30 labelled jev-scope cases

The rubric (`agent/skills/evaluate-jev/references/rubric.md`) needs at least 30 labelled
sufficient-input cases with at least 5 `overreach` labels before any threshold or routing change.

1. Open the **Labelling queue** tab (source `jev-scope`). It lists unlabelled, sufficient-input
   cases (task source `current`/`carried_forward`, unclipped objective), round-robin across P(yes)
   bands, highest band first.
2. Open a case, read the request, plan and activity (state is shown pretty-printed; the session
   transcript path is shown for deeper checks). Decide from the state using the rubric R2 tests
   (required? smallest?), before weighing Jev's answer.
3. Press `1` overreach, `2` no_overreach, `3` uncertain. `j`/`k` move between cases, `n` focuses the
   note. The label is appended to that session's `jev-watchdog-requests.jsonl` as a
   `reviewer_outcome` record, the same format `/jev-label` writes. A second label or a request with
   no outcome is rejected. Notes cannot be stored in that record, so they go to
   `runs/scope-notes.jsonl`.
4. Watch the progress bar until both minimums are met, then open **Metrics** for the confusion
   matrix at your chosen threshold (default 0.9) and the 0.50-0.95 sweep. Uncertain labels are
   excluded from the matrix; unlabelled cases are excluded.

### Policy versions

The current jev-scope policy version is derived from the data: the `policy_version` of the newest
case by timestamp (`unversioned` is ignored). The Labelling queue and its progress counts use
current-version cases only, whatever the selector says. Cases and Metrics default to the current
version; the version selector lists every other version as "legacy" so those cases stay inspectable
(`version=` on `/api/cases` and `/api/metrics` takes `current` or an exact version).

### Reviewing first-pass proposals

If `runs/scope-proposals.jsonl` exists (one row per case: `id`, `label`, `agreement`
agreed/disputed, `rationale`, `namedChange`, `evidence`, `labellers`), each case shows a "First-pass
proposal" panel above Jev's answer. Proposals are never labels. Press `a` (or "Accept proposal") to
write the proposed label through the normal human path with the note `accepted first-pass proposal`,
or `1`/`2`/`3` to override; an owner label that differs from the proposal is shown as overridden. In
the Labelling queue choose the "Review proposals" order (disputed first, then proposed `overreach`,
then the rest by P(yes)); the progress line adds "proposals reviewed: X / Y". Malformed rows are
skipped.

## Policy sources

Each new policy extension writes `DecisionRecord` lines to
`~/.omp/agent/jev-audit/<policy>/<date>.jsonl`. Every policy directory appears automatically in the
source picker. The label vocabulary is the record's own `labels`. Labels are appended to
`<policy>/labels.jsonl` through the shared `appendLabel` (one label per decision, optional note).
Metrics show verdict x label counts, plus a threshold sweep when a policy has a binary score; cost
and latency are summarised from the records.

## Case sets

For any other Jev use, put rows in `casesets/<name>.jsonl` (runtime-only, gitignored; the Playground
can also append to it):

```json
{
  "id": "case-1",
  "state": { "text": "..." },
  "questions": { "q": { "type": "noul", "instructions": "..." } },
  "expected": { "q": true },
  "note": "why"
}
```

`expected` maps question ids to a choice label (string), a boolean (noul, P >= 0.5) or a number
(score, within 0.5). **Run case set** sends every case live and reports checks passed. Names use
`[a-z0-9_-]`.

## Replay and Playground

Replay re-sends a stored state and questions and shows fresh next to stored answers. Stored state is
redacted, so a replay can differ from the original call. Results are saved under `runs/`
(runtime-only, mode 0600).

## Security

- Binds `127.0.0.1` only; requests with a non-loopback `Host` are refused (DNS rebinding).
- A random per-process token is injected into the page and must be sent in the `x-jev-lab-token`
  header on every mutating or live-call endpoint; cross-origin `Origin` values are refused.
- The API key is never logged, returned or written; response and run-file text is scrubbed of it.
- Audit files are appended with `O_APPEND|O_NOFOLLOW`, mode 0600. Stored audit text is already
  redacted by the extensions.
- This is a local convenience, not a sandbox: other processes running as you can read the audit
  files anyway.
