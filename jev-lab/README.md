# Jev lab

Three things live here:

- **Upstream demo lab**: the lab shown in the "10 Levels of Jev" video, cloned at a pinned commit
  into `upstream/` (runtime-only, gitignored). It is a demo; nothing you label there is kept.
- **Workbench** (`server.ts`, `lib/`, `public/`): the local labelling and evaluation tool for every
  Jev use in this repo. **This is where labels live.** Original code, no build step.
- **Fastino benchmark** (`fastino-bench/`): replays Jev decisions against Fastino GLiDE and
  GLiNER-2.5-Decide; see `fastino-bench/README.md` and `fastino-bench/results/REPORT.md`.

## Start

```sh
bun ~/.omp/jev-lab/server.ts            # workbench, http://127.0.0.1:4398
~/.omp/jev-lab/scripts/upstream-lab.sh setup   # once: clone + build the upstream demo
~/.omp/jev-lab/scripts/upstream-lab.sh run     # upstream demo, http://localhost:4399
```

The workbench credential is Agent Vault when started through `agent-vault-launch` (no key; the
vault adds it at the proxy), else `OPENROUTER_API_KEY`, else the stdout of `omp token openrouter`,
captured at startup. Without one, Replay, Playground and case-set runs are disabled (visible
notice); labelling and metrics still work.

## Agent first-pass labels

Labels written by blind LLM labellers carry `reviewer: "agent"`. They count toward the metrics and
show as `agent: <label>` until you confirm or override them; the latest human label always wins, and
a human label is never overwritten by an agent one. Apply a batch with
`bun jev-lab/scripts/apply-agent-labels.ts <labels.jsonl>`, one `{source, id, label, note}` per line
(`source` is `policy:<name>`); cases that already carry a label are skipped. The **Cases** filter
`agent` lists them.

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
