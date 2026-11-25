# Suite growth, pruning and time

Read this when reviewing or deleting tests, migrating tests to the skill's rules, or when a change adds several tests
or changes the suite's run time.

## Growth

- One behaviour name covers all its parameter rows.
- A new file, internal branch or implementation detail is not a new behaviour.
  Add no speculative cases outside the change's blast radius.
- When a change adds many tests, check up to 20 new names against what their tests assert; when more than 2 mismatch,
  check every new name. Rename, rewrite or remove each mismatch before handing over.

## Review and prune

- For each test you delete or rewrite, name its behaviour and the test that covers it afterwards.
- Rewrite brittle internal assertions through the public entry point or a pure-core property.
- A duplicate drives the same call path with inputs from the same input class and makes the same assertion, whatever its name.
  Different sides of a threshold are different classes. Shared setup alone is not duplication, and similar file names prove nothing.
- Before deleting a duplicate, fold any valuable intent (an input class, an edge value) into the retained case;
  ticket IDs, dates and comments are not intent.
- Remove fake echoes and tests with no reachable user path after keeping any real coverage they hold.
  Remove fixtures and support code that no test uses.

## Migrating a pattern

- Migrate one divergent pattern at a time, in the paths the user names, and leave the rest of the suite unchanged.
- Route a rewrite through the real module first; add a production input only when the real module reaches a clock,
  process or external service, and a substitute only for an external boundary.
- A T2 test that sleeps on the real clock becomes a bounded wait on an observable condition; never add a test-only flag or variable.
- Put a new shared helper where the repository's instruction files place helpers, and never where they forbid it.

## Time

- Fix cost first: remove real-clock waits, bound the waiting object, isolate contention, cut steps, and remove redundant coverage
  and wrong-tier work; never delete required coverage.
  Without a per-test limit, a T1 test over 100 ms is a hint to look for real I/O, a real clock, an unbounded wait or excess steps.
- Keep one scenario per test, never split work to hide its cost, and give test parallelism one owner (no nested worker pools).
- Recheck each deferral when its test or its tier's budget changes, and ask the user to lift it once the test fits.

## Reporting

- When the task concerns run time, or T1 is visibly slower than before the change, report baseline and head
  test counts and T1 wall-clock durations, with the command and the machine.
- A performance claim needs repeated runs of baseline and change on the same machine under the same conditions;
  a single run or a smoke run makes the claim provisional.
- Count and timing deltas are operational evidence, not claims of behavioural improvement.
