# Structure and naming label guide

Version `@6`. Each unit case is one unit that a pull request introduced, extracted exactly as
`structure_review()` extracts it (`structure-state`, `judgment_unit`). A labeller sees the case's
`unit_state`, the questions it is labelled for and this guide; never Jev's scores, the sampling
stratum or another labeller's label. For each question give 1 when the violation the question asks
about is present, else 0, the rule ID that decided it and a one-sentence reason that cites the
state. Each `same_thing` case is one code-proposed name pair (`structure_review()` runs
`name-pairs`); its labeller reads the source at the pair's commit.
`abstain` with a reason is allowed when the state cannot decide; it never counts.

## Rules per question

Each rule quotes the question's criteria in [questions.json](questions.json).

- T1 `technical_kind_grouping` (one code-proposed file in a technical-kind folder, asked as the
  `own_capability` question; shadow mode):
- Yes: The file serves one capability or domain: its symbols model, call or serve that capability's
    records or workflow, and its importers sit in that one capability. Example: a billing invoice
    model in a `models` folder that only billing code imports, or a `services` file whose functions
    run only the checkout workflow and are called only from checkout code.
- No: The file is shared by several owners or has no domain of its own: a shared contract or type
    that importers in several capabilities read, such as a base record class or an event schema
    imported by billing and shipping code; a generic helper with no domain, such as string, path,
    retry or logging helpers; code in a repository that is one capability as a whole, such as a
    single-purpose tool whose every module serves the same job; or the kind folder's own
    infrastructure, such as a base class or registry that the other files in that folder build on.
  - The council's guide for the labelled files is
    [questions/technical_kind_grouping@a856af7e3d083f02f854ab8dab200709d9cc2124a8fa21ab7c420324c2c594db.md](questions/technical_kind_grouping@a856af7e3d083f02f854ab8dab200709d9cc2124a8fa21ab7c420324c2c594db.md).
- N1 `same_thing` (one name per concept, asked per name pair in both orders):
  - Yes: Both names denote one thing in this repository: every use of one could be renamed to the
    other without changing meaning, and no owner, contract, mechanism, unit or third-party API sets
    them apart. This covers an alias binding used interchangeably with its target, a retired name
    still used for the renamed thing, and a constant whose name uses a different term from the
    value it holds, such as `DEFAULT_STORE = "repository"` where store and repository mean the same
    thing.
  - No: The names denote different things a reader must keep apart: a target and the component that
    enforces it (`latency_budget` and `Throttler`), an adapter and what it wraps (`StripeClient`
    and `Charge`), rows and cols, equal values with different roles (`timeout_seconds = 30` and
    `max_retries = 30`), or one name is a third-party API name the code must match.
  - The council's guide for the labelled pairs is
    [questions/same_thing@58135b4af5078201bee9ca6e4699458f3401e74c7d3b560cdfa3416e3a96c237.md](questions/same_thing@58135b4af5078201bee9ca6e4699458f3401e74c7d3b560cdfa3416e3a96c237.md).

- C1 `consumer_placement` (one code-proposed runnable file, asked as the `kind` Choice in two
  option orders; a finding is mean P(`finished_tool`) at or above the threshold):
- `finished_tool`: A dashboard, report tool or operator console that people run to view or operate
    on what the package produces: it loads the package's results, runs or stored data and shows them
    as pages, tables, charts, a printed report or interactive commands.
- `package_cli`: The package's own command-line or server entry point: it parses subcommands or
    serves requests that start the package's main job, such as running, building or serving what the
    package does.
- `support_script`: A fixture generator, test executable, migration, build or packaging script: it
    prepares test data, runs tests, changes a schema or builds a release for the developers of the
    package.
- `library`: Code other code is meant to import: classes and functions that callers use, with at
    most a small demo or self-check under a main guard.
  - The council's guide for the labelled files is
    [questions/consumer_placement@b85c890c3a97490cc4553a1d3d28222d4b874ed98bf8292b223c8655dfdb2cdb.md](questions/consumer_placement@b85c890c3a97490cc4553a1d3d28222d4b874ed98bf8292b223c8655dfdb2cdb.md).

## Shared rules

- X1. Deterministic checks own banned words, versions or dates, ecosystem casing, a child repeating
  its parent, plurals, test placement and import direction: these never make a label 1.
- X2. Judge only the supplied revision; a rename or commit message is not proof of either name.
- X3. Missing or truncated evidence (`evidence_limits`) is not evidence of absence. When the state
  cannot decide, abstain with the reason instead of guessing.
- X4. A mechanism name is the name of the mechanism the folder implements (`process_checks`
  implementing process-compliance checks); keeping it is never an N1 finding by itself.
- X5. Runtime data inside code, entry modules below a package's top tier and code repeated
  across folders are deterministic checks (STRUCT.runtime-data-placement,
  STRUCT.entry-point-placement, STRUCT.overlapping-responsibilities), not questions; they never
  make a label 1.

## Worked examples

- T1, label 1: `src/pages/`, `src/api/` and `src/utils/types.ts` for runs beside a `quality/`
  feature folder holding the same roles.
- T1, label 0: `harness/` holding one harness definition and the per-harness extraction keyed off
  it.

## Cases and sampling

- Cases come from first-parent history of raidar, scout, enaible-analyzers and oh-mi-pi-config:
  one row per introduced unit, from the commit that introduced it (`base` = first parent).
- Jev scored every case once; per question the labelled sample is the 60 highest scores, 30 drawn
  at random between the 90th percentile and that cut, and 30 drawn at random below the 90th
  percentile. `thresholds.json` records each stratum's population for weighted estimates.
- The tuning unit is `<repo>:<folder>`, recorded as `unit_rule`.
- `same_thing` cases are 160 code-proposed pairs sampled across 7 repositories plus 40 synthetic
  pairs; their tuning unit is the repository.

## Labels

- First pass: Claude labels every sampled question blind. The innovation council then reviews the
  complete label set in batches, blind to scores; Claude arbitrates each disagreement blind to
  scores and records `confirmed: individual` and `blind: true` (provenance `arbiter`).
- Each decision's version key hashes `{question, state, model}` (compact sorted-key JSON).
  A yes means the named violation is present. Every yes blocks merge.
