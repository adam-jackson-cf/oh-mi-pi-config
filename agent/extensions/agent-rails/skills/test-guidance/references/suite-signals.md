# Suite signals

## Objective

Name the evidence each inventory area needs, the rule group it is judged against, and the fixed criteria for each verdict.

## Guidance

- Each area below is one `## <area>` heading in `inventory.md` and one practice verdict in the rule map.
  Search hints name common idioms per stack; record what the repository actually uses, with `path:line`.
  Files from an earlier run of this skill are not evidence.
- **Verdicts.** `aligned`: the practice meets the rule group. `divergent`: tests or code break it (cite up to five examples
  and the total). `absent`: the group applies but nothing addresses it. `not applicable`: the construct it governs does not
  exist; a group that forbids something is `aligned` when the searches find none. `unknown`: the declared searches cannot
  decide; state them. A practice that meets a rule in its own way (a fixture factory, a `clock` module) is `aligned`.
- **Counting.** Count tests: one test function or case counts once however many matches it holds. Step 1 counts test
  definitions, a parametrised test once; Step 3 replaces those counts with collected test IDs, one per parametrised case.
  Benchmark functions (for example Go `Benchmark*`) are not tests unless a tier command runs them.
- **Tiers and selection**: runner config (`pyproject.toml` `[tool.pytest.ini_options]`, `pytest.ini`, `conftest.py` markers;
  `vitest.config.*` projects, `jest.config.*`; Go build tags), each tier mechanism (marker, file-name suffix, build tag,
  folder) with the tag it maps to, whether files are grouped by feature or by tier, and test files no tier or two tiers
  select (a tier is a T1, T2 or T3 tag, not a command; a file several commands run is not double-selected). A file
  whose design does not match its tier is a migration candidate, never a separate tier line. A tier has a complete
  command when one invocation selects every test of the tier and nothing else. Judged against: writing-tests `Tiers and assertion owners`.
- **Commands and checks**: test and static-check commands in every command surface (shell scripts, package scripts,
  `tox.ini`, `noxfile.py`, a task runner, a Makefile), the commit hook, CI workflows and what they run on pull requests,
  every scheduled workflow and what it runs, the fix command and the coverage config with its floors.
  A hook is installed only when `git config core.hooksPath` or `.git/hooks/<name>` holds it; a config file alone is
  `configured`. A benchmark command is not a check. Judged against: index `Every task`.
- **Public entry points**: package manifest entries (`[project.scripts]`, `exports`, `bin`, `cmd/`), package `__init__` or
  index re-exports, and what tests import. `divergent` counts tests that import a name no entry point exports; importing
  an exported name through its defining module is aligned. A name is exported when a manifest entry names its module
  or the language makes it public (in `__all__`, or without `__all__` a name and module path with no leading `_`;
  `export` in JavaScript and TypeScript; a capitalised name in Go). Calls to private names belong to Assertions and cases.
  Judged against: writing-tests `Writing outcome tests`.
- **Layout and naming**: test roots and file patterns, fixtures and support folders, and the helper home any instruction
  file names. One root folder per tier (such as `tests/unit` and `tests/integration`) contradicts grouping by feature,
  even when files inside each root follow owners. Sample up to 20 test names: list test definitions in path order and
  take every Nth starting with the first, N the total divided by 20 rounded up. `aligned` when at least four fifths of the sample state
  behaviour, condition and outcome (the condition names the input, state or variant that leads to the outcome, an
  adjective such as `discounted` included); a weak name found outside the sample is recorded in the inventory and
  changes neither the verdict nor the migration candidates.
  Judged against: writing-tests `Names and placement`.
- **Assertions and cases**: assertions on private state (`._name`, internal attributes) and calls to private names,
  call counts (`assert_called_once`, `toHaveBeenCalledTimes`; `toHaveBeenCalledWith` on a substitute for an external
  boundary is not one), error prose matched in full, expected values copied from implementation output, and duplicates
  (same call path, same input class, same assertion). Judged against: writing-tests `Choosing cases`, `Writing outcome tests`.
- **Self-mocking and test hooks**: patches of the repository's own modules (`mock.patch("<own package>`,
  `monkeypatch.setattr(<own module>`, `vi.mock('./`, `jest.mock('../`, aliased own-package paths), and production code
  that reads test-only flags or variables (`TESTING`, `PYTEST_CURRENT_TEST`, `NODE_ENV === 'test'`). Judged against:
  index `Every task`.
- **Production inputs**: how time, randomness, IDs, process runners and external clients reach code (`time.time`,
  `datetime.now`, `Date.now`, `setTimeout`, `time.Now`, `random`, `uuid`, `subprocess`), and tests that sleep or read
  real clocks. Judged against: writing-tests `Writing outcome tests`.
- **Isolation and timeouts**: temporary-directory use, HOME and XDG handling, shared output paths, process calls or waits
  without a positive timeout, and the per-test timeout config. Judged against: writing-tests `Writing outcome tests`.
- **Evasion and retries**: skip, xfail, only and focus markers, retry plugins or options (`--reruns`, `retry:`), CI reruns
  and raised timeouts, each with its stated reason. Judged against: index `Every task`.
- **Boundary tests and journeys**: T2 tests, how they launch the product (subprocess, HTTP, browser), runnable entry
  points and required environments. Judged against: `boundary-tests`.
- **Property tests**: property libraries (Hypothesis, fast-check, `testing/quick`, gopter), existing property tests, and
  code whose job is ordering, encoding or numeric representation. Judged against: `property-tests`.
- **Golden files**: golden or snapshot files (any stored file a test loads as the expected output or reference examples
  it compares against, such as `*.golden`, `testdata/` or fixture baselines,
  `__snapshots__/`, `toMatchSnapshot`, syrupy), where they live, the commands that verify and regenerate them
  (a test flag that rewrites them, such as Go `flag.Bool("update"`, counts), and any change record a check requires when
  a golden changes. Judged against: `golden-files`.
- **Migration candidates**: one pattern per area whose verdict is `divergent` in test files, with that area's test count
  (for duplicates, the copies to delete).
  Rank patterns by test count; ties keep the order of the areas above. For duplicates, the exemplar is the copy the
  suite-maintenance rules would delete. Otherwise a pattern's exemplar is the first test in path order (by file path,
  then position in the file) that needs no production change (any edit to production code) and no change placement
  forbids (gate configuration), else the first test needing only a production change, marked as such; a pattern with
  neither has no exemplar and the next pattern takes its slot. When designing the rewrite at Step 4 shows the mark was
  wrong, re-pick by the same rule and show the revision in the plan.
