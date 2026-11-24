# Decision store, taxonomy and shadow review

## Objective

Keep every judgment and check decision queryable in one place, classify judgments so they compare
by kind, and run a judgment whose precision cannot be measured yet without letting it act.

## Guidance

- [Judgments](judgments.md) and `prove()`'s checkers write the store; rails write its `rail_events`.

### Taxonomy

- Every decision carries its place in the [jev-actions design patterns](../../../../../skills/jev-actions/references/design-patterns.md)
  vocabulary, so the store can compare judgments by kind; `load_judgment` refuses a missing field or
  a value outside it:

| Field | Values |
| --- | --- |
| `approach` | `Gate`, `Route`, `Rerank`, `Select`, `Grade`, `Verify`, `Eval judge` |
| `overlays` | Distinct items of `Face-value`, `Changing state`, `Chain`, `Place` |
| `primitive` | `noul` (a bool question), `choice`, `score` |
| `evidence_unit` | `item`, `pair`, `placement` |
| `combination` | `jev_only`, `proposer_product`, `chain_all`, `any`, `max_per_unit` |
| `action` | `block`, `confirm`, `review_queue`, `log`, `route` |
| `policy_area` | Kebab-case, such as `structure`, `naming`, `scope`, `security`, `evidence` |

- Each load registers every question version, its taxonomy, mode and decision in the store's
  `questions` table.

### Shadow decisions

Each decision in `thresholds.json` has `mode`. An `enforced` decision acts on its outcome. A
`shadow` decision is for a judgment whose precision cannot be measured yet: it is asked and logged
exactly like an enforced one, every finding is shown as "shadow, not blocking", and it never blocks,
refuses or halts. Its `shadow_exit` (`{"labelled_flags": n}`) says when review has measured enough
to decide enforcement. Only bool decisions can run in shadow mode. No shipped judgment does today.

### Shadow review

`guardrails shadow-review` (run from `agent/extensions/dynaflows/engine` with `uv run --project .`)
labels logged decisions and measures shadow questions:

| Command | Does |
| --- | --- |
| `export <judgment> [--question Q] [--sample-passes N]` | A blind batch (`{guide, items}`): unreviewed flags plus sampled passes, with state |
| `import <labels.json> [--labeller council\|arbiter]` | Council or arbiter rows into `reviews`, verdicts and the judgment's eval data |
| `report [<judgment>]` | `shadow_progress` and precision at the cutoff with flag counts per question |
| `inspector <judgment> [--verdict V] <out.jev.json>` | Jev Inspector session files rebuilt from payloads |

- Items carry `label_id` (the decision id); labellers never see scores or outcomes.
- An import appends the case and label to the judgment's `cases.jsonl` and `labels.jsonl`, the
  permanent record, then deletes payloads no unreviewed decision needs. An arbiter label overrides
  the council's.
- `--machine nuc` copies the NUC's store read-only over `ssh` (BatchMode) and reads it alongside;
  labelled NUC decisions are copied into this machine's store.
