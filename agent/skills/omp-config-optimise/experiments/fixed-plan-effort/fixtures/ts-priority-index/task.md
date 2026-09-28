# Task

Urgent work at the front of a queue is currently skipped by `getPreferredIndex`; the bug is in its
conversion of the `Array.findIndex` result, not in dispatch ordering. Apply this exact bug-fix plan.

1. In `src/priority.ts`, leave the `findIndex` predicate and public signature of
   `getPreferredIndex(entries: readonly QueueEntry[]): number | undefined` unchanged.
2. Replace only the truthiness conversion of `index` so that a `findIndex` result of `-1` returns
   `undefined`, while every non-negative index, including `0`, is returned unchanged.
3. Do not change `src/dispatch.ts`, its fallback ordering, the `QueueEntry` type, or exports.

Acceptance criteria: an urgent entry at positions 0, 1, or later is selected; no urgent entry
returns `undefined`; `dispatchOrder` consequently puts the first urgent entry first without changing
the relative order of all other entries.
