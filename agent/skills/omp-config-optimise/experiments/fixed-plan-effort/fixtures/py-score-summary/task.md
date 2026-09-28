# Task

`scoretool.py` already reads one numeric score per non-empty line and supports its existing count
command. Add its requested summary mode using this fixed plan.

1. In `scoretool.py`, add `summarize_scores(values: list[float]) -> dict[str, int | float]`; it must
   not mutate `values` and must return its keys in this insertion order: `count`, `unique`, `mean`,
   `median`.
2. For an empty list return exactly `{"count": 0, "unique": 0, "mean": 0.0, "median": 0.0}`. For
   non-empty input, `count` includes duplicates, `unique` counts distinct numeric values, and
   compute the mean as `round(sum(values) / count, 2)`.
3. Sort a separate copy for median: the middle value for odd counts, or the arithmetic average of
   the two middle values for even counts; return the median as a float.
4. Add a `--summary` boolean flag while retaining `--input` and the existing `--count` behavior.
   When `--summary` is present, read the file with `read_scores` and print
   `json.dumps(summarize_scores(scores))` as one line; if both flags are present, print the summary
   only.
5. Import only the standard-library `json` needed for this mode; leave parsing of files and existing
   count-only output unchanged.

Acceptance criteria: empty input, duplicates, input ordering, odd/even medians, two-decimal mean
rounding, and `--summary` precedence behave exactly as above.
