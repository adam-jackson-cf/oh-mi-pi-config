# Tiers

## Objective

Define how the repository's tiers, tags, budgets and deferrals are recorded, selected and rendered in the installed index.

## Guidance

- **Vocabulary.** Runbooks say T1, T2 and T3 and the tags `boundary`, `live`, `exploratory`, `mutation` and `load`, plus
  `deferred` for a deferral. The index maps each repository tier mechanism (marker, file-name suffix, build tag or folder)
  onto a tag; a mechanism that names speed alone (such as a `slow` marker) is a conflict.
- **Budgets.** A tier's budget is the wall-clock time its whole run may take on its schedule. Read it from the repository,
  first that exists: a check that compares measured run times with a limit and fails when one is over (a whole-tier or
  per-test limit, per machine when the check keeps them per machine), with its limits and that check as the source; a
  documented limit for the tier; the timeout of a CI job that runs that tier and nothing else (a job shared with other
  tiers or checks sets no tier budget). A timeout that stops a run, per invocation, package or test (such as a
  pytest-timeout or `go test -timeout` value), is a hang guard, not a budget. Record the source. A limit a check enforces
  is a repository fact: never propose changing it. Otherwise Step 2 asks the owner, with defaults of 2 minutes for T1
  and 15 minutes for T2. T3 has no budget.
- **Deferrals.** A test whose design tier's run would break that tier's budget, after its cost is fixed, is deferred to
  the next schedule as writing-tests states. Record each existing one: a test that is T1 or T2 by design but runs only on
  a later schedule (a release or on-demand job, or a marker every earlier command excludes), with the repository's
  stated reason or `reason not recorded`. Running a deferred test later is not a conflict; a marker naming speed alone
  still is. Under `merge` or `full`, adding the `deferred` tag and its reason to those test files is an optional row,
  default no. Never defer a test or lift a deferral without the owner.
- **Tier commands.** When no single invocation selects every test of a tier, the tier line records the run form of the
  selection Step 3 collected (the same selector without the collect-only option); when only a list of files or packages,
  or an exclusion naming them, selects a tier, the line lists them and adds `new files: ask the user where they go`
  (`new packages` when packages are listed). A tier's command leaves out its deferred tests, and the deferred line
  selects them. No option adds or edits a tier command, a command surface or the runner's collection settings. A tier
  with no complete command and each test file no tier selects is a mechanism gap: Tier facts record it, and the result
  lists it as a follow-up for the repository's owner. Until collected, its tier line names that path.
- **`tier_map`** renders one line per tier and tag:
  `- T<n> (<tag, or untagged>; repository <mechanism> `<name>`): selected by <selector>; run `<command>` <when, as
  writing-tests states, with the job that runs it>; budget <budget> (<source>).`
  A tier with deferred tests adds `- T<n> deferred (<tag>): selected by <selector>; run `<command>` <when the later job runs>.`
  A tier with no tests reads `- T<n>: no tests yet; ask the user where the first one goes.`
  A per-test limit a check enforces adds `; per test <limit> (<check>)` to its tier's line. When a check reads approved
  deferrals from a record, add `- Deferral approvals: recorded in <path> as <entry form>.`
- **Deferral decisions.** Each deferral adds a `Repository decisions` bullet: `- Deferred T<n>: <path> (<reason>; <cost>
  against the <budget> budget); runs with <job>; approved <date, or "existing repository choice">.` When a check reads
  approved deferrals from a record, the approval goes there in that record's form, and the bullet ends `; approval in <path>`.
