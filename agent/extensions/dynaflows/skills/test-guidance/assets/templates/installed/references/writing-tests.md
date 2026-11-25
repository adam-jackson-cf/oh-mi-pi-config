# Writing tests

Read this when writing, changing, moving or deleting a test.

## Tiers and assertion owners

- Choose a test's tier from its design first, what it drives, then check its cost fits that tier.
  Speed alone never moves a test: a fast boundary test stays T2, and a slow in-process test is a T1 test to fix.
- T1 (untagged; every change, locally and per commit): outcome tests through the public entry point with in-memory
  state, or files in a unique temporary directory, and property tests. No process, network, real clock or sleep, in setup
  and teardown too, and fast enough for every change. A failure blocks the change.
- T2 (tag `boundary`; before merge, and the T2 tests a change affects locally): a real process, build, transport,
  server-database or loopback seam, or a scripted journey through the real entry point, all offline. A failure blocks merge.
- T3 (scheduled, by hand or as a release gate, each tag its own job; never when handing over a change): tag `live` for
  services outside the machine, live or paid (local tools and processes are T2 however costly), `exploratory` for
  non-deterministic runs such as agent exploration, `mutation` for mutation testing, and `load` for soak, stress and
  performance runs. Their design makes them too costly or unstable for every merge. When several tags fit, use the
  first in that order. A failure is triaged as an issue; it blocks a release only where the repository gates releases on it.
- Each tier has a budget for its whole run, as the skill's tier lines state. When a test would break its tier's budget,
  fix its cost first (suite-maintenance `Time`). If it still cannot fit, ask the user to defer it: it keeps its design tag,
  adds the tag `deferred` and runs on the next schedule (a deferred T1 with T2 before merge, a deferred T2 by hand or as a
  release gate). Record the reason and the budget it breaks in the file, for example
  `pytestmark = [pytest.mark.boundary, pytest.mark.deferred(reason="full workflow, about 6 min against the 15 min T2 budget")]`,
  a `.boundary.deferred.test.ts` name with a first-line `// deferred: <reason>` comment, or a Go `//go:build boundary && deferred`
  line with that comment. Never defer a test, or remove a deferral, without the user; record the approval where the skill's
  deferral approvals line says, else as a Repository decisions bullet in the skill.
- Give each assertion one owner. T1 owns the exact values of each input class; a T2 test asserts what crosses the boundary,
  with one representative value per path; a T3 test asserts trends or thresholds. A T1 and a T2 test of one value are not duplicates.
- Add a T2 journey only when the outcome crosses a process, build or UI boundary that no existing T2 test proves.
  A new rule behind an existing boundary gets T1 cases; when an injected clock or runner lets T1 test the wiring, add no T2 case.
- On a move, move the fixtures too and delete the superseded copy.

## Choosing cases

- Start from the requirement. Name the user path and a realistic input for each case; drop impossible cases.
- Write one case per behaviour and per input class that changes the outcome. Each side of a threshold is its own class:
  test just below it and exactly at it.
- Cover user-reachable errors, side effects and preserved neighbours. For an adapter, cover success, failure, timeout and malformed replies;
  for a gate or checker, prove both acceptance and refusal.
- Extend an existing scenario before adding one. Prefer no new test to a bad test.
- Never write wiring-only cases (asserting only that one part called another, with no observable outcome), fake echoes,
  tests of private details, or tests that pin only a fixed value or changed text.
- For a bug, cover representative members of the failure class and report whether existing tests protected the broken rule.
- Show each new or changed behaviour's test failing on the pre-change code, then passing after the change.
  A test added for behaviour the code already has cannot fail before; show it failing against a deliberate temporary break
  of that behaviour, then undo only that break.

## Writing outcome tests

- Drive the public entry point (anything a package exports, including exported types and fields) with realistic inputs.
  Call internals only through it (property tests excepted); copy the import path sibling tests use for the same name.
  If a changed area's entry point is unclear, ask the user which interface its callers use, and never test a guessed
  private function.
- Derive the expected value from the requirement, never from the implementation.
- Imagine two edits: a harmless refactor must leave the test green, and breaking the claimed behaviour must fail it.
  Rewrite or delete a test that fails either check.
- Assert returns, records, exit codes, files, messages and errors callers branch on (by type or stable code).
  Never assert private state or call counts. When only a message tells two errors apart, match its stable part and report the gap.
- Share a recorded or local substitute for an external boundary through the support code like any helper, behind the
  production adapter; never copy one substitute into each test that needs it.
- Supply time, randomness, IDs, process runners and external clients through production inputs. An input with a production default
  is a production input; adding one is a design change to justify in the PR. Never add test-only flags, variables or hooks to production code.
  Code nothing in production calls or exports is unused: report it instead of adding inputs to it.
- Count fixed steps instead of sleeping. For a threshold the change touches, cover the step before it and the step at it;
  drive injected clocks with values exact in binary (integers, halves, quarters).
- Put a positive timeout on every process call and wait, on the object that waits, not only on the test.
- Keep files in a unique temporary directory. A test that touches user files points HOME and XDG directories at a temporary
  directory in its own fixture; no test may change a file under the real home directory.
- Reach coverage floors only with outcome or property cases or by deleting unreachable code; add no exclusions or no-cover pragmas.
  T1-only gaps do not prove code dead.
- Record a known uncovered defect with its issue ID instead of running it without an assertion.

## Names and placement

- Name each test for behaviour, condition and outcome; check the name matches what the test asserts.
- File names mirror the module or feature they test and carry no ticket, version or date tokens. Follow sibling tests.
- Group test files by feature or owning code area; one folder may hold every tier. Each file has one tier: tag a T2 or
  T3 file as the skill's tier lines show, leave T1 files untagged, and check placement with the runner's collect-only listing.
- Create a folder with its first file, never as empty scaffolding.
