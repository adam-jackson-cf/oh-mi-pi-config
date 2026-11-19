# Blind label contract

## Signature

```python
blind_label(batch_files, rubric_path, labels, labellers=("scout", "reviewer"))
```

Frozen JSON batches are a list or `{cases: [...]}` with unique nonblank string
case IDs. Labels must be a nonempty list/tuple of nonblank strings. The labellers
argument is exactly scout and reviewer, in either order, supplied as a list/tuple.
They receive separate blind read-only sessions containing only batch/rubric and
quote deciding evidence. This promises read-only sessions, not a cross-family
guarantee or code verification. Two jobs per batch, at most eight batches/16
agents; excess raises, no implicit waves.

JSON-safe output uses lists: `agreed:[{batch,id,label}]`,
`disputed:[{batch,id,votes}]`, `failed:[{batch,labeller,error}]`. Agreed/disputed
records follow batch/case input order; `batch` is a zero-based index. `votes` maps
labeller names to their `{id,label,evidence,rationale}` answer records.
Only input IDs appear. Missing/duplicate answers remain disputed; unknown answer
IDs invalidate the labeller's batch. Failed/off-schema results are not votes.
The orchestrator settles disputes; a human confirms every label before writing.
The helper itself writes no labels.
