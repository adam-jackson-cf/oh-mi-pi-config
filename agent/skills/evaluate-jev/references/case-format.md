# Case format

`scripts/summarize.ts` reads one JSON object per line. Each process's profile names how its records
become this format: the process writes it directly, or a small exporter converts its own logs.
Other fields are ignored and not copied to `--cases-out`; exporters may still add process-specific
context for scorers who read the exported file.

Dependencies point one way: this skill's scripts stand alone (standard library plus `zod`) and own
the shared metrics in `scripts/cases.ts`. Local tools such as a labelling workbench import those
metrics, and each process's exporter lives with the process. Never import local tooling into the
skill's scripts.

## Case lines

- `type`: `"case"` (default)
- `id` (required): Stable case id, used to join labels
- `verdict` (required): The process outcome: an answer (`yes`), an action (`block`), or
  `error:<reason>` / `no-outcome`
- `timestamp`: ISO 8601; used for windows and for the `current` default (the version of the
  newest record, a heuristic: old sessions may still write old-version records)
- `version`: Process or question version; missing means `unversioned`
- `stage`: `deterministic`, `jev`, `jev_error`, `no_outcome`, or a process-specific stage
- `subject`: Short redacted description of what was judged
- `state`: The redacted state Jev was shown
- `questions`: The questions as sent (TypeSafe question objects keyed by question id)
- `answers`: Array of `{id, type, noul?, choice?, probabilities?, score?, confidence?}`
- `score`: Headline score in 0..1; derived from the first answer (noul, else P(yes)) when absent
- `label`: Effective label, if already labelled
- `labelBy`: `human` (default when `label` is set) or `agent`
- `labelOptions`: Label vocabulary for this case
- `positiveLabel`: The label that counts as positive for confusion and threshold sweeps. These
  predict from `score >= threshold` alone, take one positive label for the whole input and treat
  every other non-neutral label as negative. They describe process correctness only for a binary
  `reference`-label set whose positive outcome is that score cut-off. For `verdict-grade` labels or
  composed and multi-question verdicts, read correctness from `verdictByLabel` and the profile's
  mapping, and do not report the headline confusion as process accuracy.
- `sufficient`: The profile's R1 pre-check, when it can be computed deterministically
- `group`: Any split worth tallying (session kind, tenant, channel)
- `evidence`: Where the wider evidence lives (transcript path, URL, file and hash)
- `resolvedModel`: Model that answered
- `error`: Error summary; marks the case unresolved
- `costUsd`, `latencyMs`: Per-call cost and latency

## Failure lines

`{"type": "failure", "timestamp": "…", "reason": "network"}` records an attempt that failed before
a case existed. They are tallied as `failuresBeforeCase` and never counted as cases.

## Labels file

`--labels-in` takes one `{"id", "label", "by"?, "note"?, "timestamp"?}` per line. Rows apply in
file order on top of each case's own `label`: the latest human label wins; an agent label never
replaces a human one. `by` defaults to `human`. When the user asks you to label cases, append rows
here rather than editing the cases file.

## Safety

Redact secrets before writing either file. `summarize.ts` prints aggregates only; `--cases-out`
writes filtered cases with effective labels at mode 0600.
