# Review and prune the suite

## Objective

Keep meaningful behaviour coverage and remove redundant or brittle tests.

## Guidance

- Classify each touched test as kept, duplicate, brittle or meaningless; name its behaviour and assertion owner.
- Keep tests that survive harmless refactors and fail when the claimed behaviour breaks.
  Rewrite brittle internal assertions through the public entry point or a pure-core property.
- Delete a duplicate only after naming the retained test that covers its behaviour.
  Fold valuable regression intent into that retained case before deleting the old one.
- Remove meaningless fake echoes and tests with no reachable user path, retaining any real coverage first.
- Remove orphaned fixtures, support code and stale map links when their final test owner goes away.
  Apply [suite-budget.md](suite-budget.md) to the resulting count and timing changes.
