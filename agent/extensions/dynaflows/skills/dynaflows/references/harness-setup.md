# Harness setup deltas

Run [generic setup](skill://setup-repo-proofs) in order. This reference adds
only the DynaFlows-specific policy, engine commands, adapter and approval mechanics. The generic
workflow remains usable without these additions; do not copy these mechanics into target repos.
Use [the project setup playbook](playbooks/project-setup.md) for the six-stage delivery sequence.

## Ownership and authoritative references

- [Record format and lifecycle](../../../../../lib/record/README.md) owns origin resolution, gate schema,
  strict loading, command arrays, owner-only paths, merge policy, CI runner roles, device rollout,
  proposal storage and write authority.
- [Record profile](../../../../../lib/record/profile.md) owns profile layout, settings, approvals,
  per-machine budgets, exclusions, ownership and verification-surface configuration.
- [Adapter authoring](adapter-authoring.md) owns the six-part evidence interface and conformance.
- The engine lives at `agent/extensions/dynaflows/engine/`; run it from oh-mi-pi-config, never
  install, pin, copy or fork it into a target repository. Its catalogue is
  accessed with `uv run --project agent/extensions/dynaflows/engine guardrails catalogue`.
- [The design directive](../../../AGENTS.md) governs ownership. Harness-owned per-edit path checks
  are the
  explicit [ADR 0002](../../../../../../docs/adr/0002-harness-owns-path-checks.md) exception.

The stage-specific instructions below preserve the existing setup obligations. Record field
mentions are consumers of the linked record documentation, not a second schema definition.
All `<omp root>` paths refer to the oh-mi-pi-config checkout, not the target.
Harness process events below apply only when DynaFlows is enabled for the target repository,
as required by [ADR 0004](../../../../../../docs/adr/0004-dynaflows-flag-gates-the-harness-process.md).
Off means ordinary Git safety and repo-native checks, not these process additions.

## Boundary

The harness/repo boundary governs every step. The target repository never depends on, contains
or mentions omp or dynaflows. It keeps only conforming code and the controls the project would
want without the harness: build, tests, its own linters and checks, gate targets, hook config and
CI job. Harness opinions and their checks live in oh-mi-pi-config. Per-repo settings live in the

## Control authority

The repo's own controls come first. Where the repo already has the capability for a control
(its own checker scripts, a configurable linter, its test runner), the rule lives there: a rule
the repo's checkers already enforce stays with them, and a missing rule is added to them by a
repo PR. The record's `guardrails.check.repo` lists the checker files that enforce each step's
rules, and `guardrails check` leaves that step out. The harness runs a step only when the
repo cannot hold its rules without depending on omp or dynaflows. It never runs a step beside a
repo checker. Step 2 defines the three outcomes; the
[design directive](../../../AGENTS.md) holds the principles behind them.
Harness path-check judges only new paths and path segments: worktree additions/renames
(including untracked) after edits, staged additions/renames at commit. Its profile settings
are `guardrails.structure`, `guardrails.naming` and `guardrails.paths`;
`guardrails.check.repo` does not delegate this check.
`gate.json` has no path-check command field; repo `checks.commit` and `checks.ci` stay repo-owned.

## Commit events and rule families

Rails run harness `guardrails path-check --repo <worktree> --record <record dir>` after edits
and before commit, using required profile `guardrails.structure`, `guardrails.naming` and
`guardrails.paths` settings. This consistent per-edit contract is the explicit principles 1–2
exception in [ADR 0002](../../../../../../docs/adr/0002-harness-owns-path-checks.md).
At each agent commit, rails then run the record's `checks.commit` in the worktree, then the harness
checks: `guardrails check --repo <worktree> --record <record dir>`, run with
`uv run --project agent/extensions/dynaflows/engine guardrails check`. It covers the whole
repo except naming, structure and path-check, which use the new-path event scope in
[ADR 0012](../../../../../../docs/adr/0012-file-parent-repeats-and-scoped-exceptions.md).
Other checks block without baselines; generated, vendored and fixture exclusions come from
the record. The repo's required CI context stays its own `gate`.

New rules enforce dependency direction, folder ownership, naming, test placement, fast-tier and
per-test budgets, justified test growth and duplicate call-path/assertion detection.
Test-guidance v2 rules add profile completeness, test-file names, tier selectors, no
self-mocking, no test hooks, no evasion, fast-tier isolation, the user-data guard, the coverage
ratchet, golden change records, identity artefacts, property seeds, mutation sensitivity and the
verification surface. Budget limits are per machine, selected by a deterministic local machine
probe and based on at least five baseline runs. Budgets apply to the local commit gate only: CI
never measures or reports test timing.

## Engine initialization

A stack gets a skill reference once two repositories need it (the rule of two). Python has one:
`uv run --project` with `agent/extensions/dynaflows/engine/`. It covers the whole repo
checkout; repositories never install, pin or copy it, and it writes nothing into the repo tree.

- `guardrails init --repo <clone> --record <record dir>` drafts the record for owner
  confirmation directly in `repos/<owner>/<name>/` or in a draft directory.
`uv run --project` with `agent/extensions/dynaflows/engine/`. It covers the whole repo
  names and, through `gh`, the default branch's required checks. It writes `gate.json` and
  `profile.md` into the record, and writes proposed native-tool configs (Ruff, mypy, import-linter)
  into its `proposed/` folder for application to the repo as approved fixes.
  It proves language-server readiness with a canary
  `references` query. It never overwrites a recorded value and never writes an approval; it
  drafts `dynaflows.enabled` true only with `--enable-dynaflows`.
- Per-repo settings live in the front matter of the record's `profile.md`. Shared style rules
  live in the reference's `guardrails/data/house-style.json`.
- The rule catalogue ships inside the package (`guardrails/data/guardrail-rules.json`).
  Each catalogue row the reference implements has a `reference` field naming the
  `guardrails` subcommand and module. The package's own tests in oh-mi-pi-config prove
  every rule the package implements, once for every repository.
- A test framework without an adapter gets one through
  [adapter authoring](adapter-authoring.md), accepted by a deterministic conformance
  run.

## Step 0: safety and modes

1. The harness checks cover the whole repository. There are no baselines and no new/changed-code
   scope. Record candidate scope exclusions only for generated, vendored and fixture paths, with
   evidence; they become record config after approval. Third-party code is excluded at the
   boundary (design directive principle 10); Step 1 maps that boundary.
2. Identify agent instructions and access restrictions before reading files. Never inspect secret
   files or protected paths without their required authorization; verify environment variables by
   presence only.
3. Select a temporary candidate root outside the target checkout and outside the records directory.
   No target writes, record writes, package installation, formatter fixes, Git staging, or CI
   changes occur in Steps 0–5.
4. Record whether the target has an existing hook, gate runner, test suite, type checker, linter, or
   documentation ownership rule. Existing files remain owned by the target until the user chooses
   keep, merge, or replace.
5. Record every setup mode that applies: `greenfield` when no record exists and the target
   has no guardrail checks; `adopt` when the repo has its own checkers or configurable tools
   that enforce, or could hold, harness rule families that the record, if one exists, does not
   give them in `guardrails.check.repo` (Step 2's `repo` and `grow` outcomes); and `extend`
   when a record exists and only a missing part is added, such as a verification surface.
   An existing record does not rule out `adopt`.

## Step 1: discovery and enforcement evidence

   Discover the inputs the record needs: make (or task runner) targets, pre-commit or other hook
   manager config, CI workflow job names, and the default branch's required checks from
   `gh api repos/<owner>/<name>/branches/<default>/protection` and
   `gh api repos/<owner>/<name>/rules/branches/<default>`. A query that fails is recorded as
   unknown, never as "no required checks".
4. List test layers and evidence: unit, integration, seam, end-to-end, smoke, coverage, timeouts,
   fixtures, and CI matrix. Do not treat a config file as active unless an invoked command reads it.
   When the record exists, read its `profile.md`; record its tiers, entry points, ownership
   areas, affected-T2 selection, verification surface and per-machine budget approvals.
5. For each bespoke checker, name the `guardrails check` step whose rule family it enforces
   (`STEPS` in `guardrails/whole_repo.py`), the rule IDs it enforces, with the source line for
   each, the `checks.commit` chain that runs it (for example make target to script) and how it lists
   files. A step's rule IDs are the catalogue rows (`guardrails/data/guardrail-rules.json`)
   whose `reference.subcommand` names it. A checker that walks the disk instead of `git ls-files
   --cached --others --exclude-standard`, or follows symlinks out of the repo, is a gap to fix in
   the repo checker. Search the repo's checkers for every rule ID of every step, and record a
   source line or `absent` for each: a claim that the repo covers or lacks a rule needs that
   evidence. Record any baseline (a file of waived findings) each repo checker or tool applies,
   with its finding count; a baselined finding is an open finding.
6. Map the third-party boundary (design directive principle 10). List the dependencies the code
   imports, the owned adapter (if any) each one is reached through, vendored or generated
   third-party paths with evidence of their origin, and every in-repo stub, copy or shim of
   third-party code. For each checker and type checker, record whether it reaches third-party code
   and how it excludes it. Record owned code that imports a third party outside its adapter, tests
   that replace a third party rather than an owned interface, and workarounds for upstream faults
   outside an adapter.
7. Map the dependency-audit scope (`SECURITY.dependency-audit`). List every lock file and
   dependency manifest that `git ls-files --cached --others --exclude-standard` returns, in every
   ecosystem. For each, record its ecosystem and the repo audit that covers it, with the source
   line that reads it, or `absent`; for a manifest, also record the lock that pins it. Record each
   excluded fixture or vendored path by name, with evidence of its origin. Each of these is a gap:
   a lock file no audit covers, a lock format the audit cannot read, a manifest with dependencies
   and no lock, an audit that runs only on staged files or on a schedule, and an audit that scores
   severity, waives advisories or prints findings in another format than the catalogue row's
   `advisory_line`. Record the repo's dependency-audit exception file, if any, for the record's
   `ownerOnlyPaths`.

## Step 2: exact rule contract

1. Read every entry in the rule catalogue,
   `agent/extensions/dynaflows/engine/guardrails/data/guardrail-rules.json`, including
   the language-neutral additions. The bundled guardrail rules are the authoritative
   contract. Treat each ID, semantic, threshold, and target-derived requirement
   as the baseline, not a generic category.
2. Map every ID to its target construct, checker/configuration, exact setting,
   scanned paths, exclusions, and local/CI runner paths. Keep universal rules
   applicable in every stack. Conditional exclusions need evidence that the
   construct is absent and explicit user approval.
3. Map `STRUCT.dependency-direction` to a layer-edge allowlist and
   `STRUCT.folder-placement` to path ownership; unmapped edges/files fail. Map
   `NAME.conventions` to path/symbol patterns and `TEST.placement` to owner/tier
   paths from the record's `profile.md` when present. Use its ownership areas and affected-T2
   selection; unmapped T2 files fail and unknown changed-code ownership selects all T2.
   Diagnostics name the file and corrective edge, name, or destination.
4. Map `TEST.budget.fast-tier-time` and `TEST.budget.per-test-time` to a checker
   consuming the normalised JUnit run report. Select limits by a deterministic local machine
   probe from the record's `profile.md`; block timing only at the agent's commit. CI never
   measures or reports test timing, so `checks.ci` never runs the budget checker. Missing,
   invalid or stale reports are fatal. The catalogue marks both rules `git_hook` evidence.
   Derive limits from at least five baseline runs. First limits and raises
   require owner approval at Step 5; unknown machines report timing only and prompt to record
   a baseline.
   Map `TEST.growth.justified` to a
   base-ref diff that requires every new test function to name the behavior
   it proves; a generic or placeholder name fails with the test name and
   rename-or-remove fix. Map
   `TEST.duplicate-path` to normalized call-path/assertion fingerprints,
   compared against existing and other new cases; duplicates fail naming both
   tests and the merge-or-remove fix.
5. Map `GATE.local-ci-parity` and `GATE.pre-commit` to the record's `gate.json`
   command arrays (`checks.ci`, `checks.commit`). Each names the repo's own command,
   never an omp path. Preserve the repo's CI commands and record its actual local/CI/hook
   invocations and failure propagation. Map `GATE.path-check` to harness
   `guardrails path-check --repo <worktree> --record <record dir>`, not a record command.
   Required profile `guardrails.structure`, `guardrails.naming` and `guardrails.paths` configure it.
   Rails run it after edits and before commit; at commit `checks.commit` and whole-repo
   harness checks follow. A repo hook remains the repo's choice.
   Record any existing pre-push hook as a practice to replace.
6. For tool rules, record selector, limit, exceptions and pinned version; one
   tool rule may carry both a `selector` and its limit `value`. A row with a
   `reference` field names the `guardrails` check step that enforces it
   from the harness, over the whole repo and configured by record settings,
   when the repo cannot hold it. Give every check step exactly one outcome
   from Step 1's
   rule-ID evidence:
   - `repo`: checkers that `checks.commit` runs enforce every rule ID the step
     reports, list files from git and apply no baseline. Map the step to the
     list of checker files in `guardrails.check.repo` (one step's rule IDs may
     be split across several checkers).
   - `grow`: the repo has the capability to hold the step's rules (its own
     checker scripts, a configurable tool, or its test runner, run by
     `checks.commit`), but a rule ID is absent, a checker applies a baseline,
     walks the disk, or is not run by `checks.commit`. The fix is a repo PR
     that adds the missing rules to the repo's checkers in the repo's own style
     and with its own tests, fixes every baselined finding and deletes the
     baseline, or wires the checker into the commit command. Once it merges,
     the step is `repo`.
   - `harness`: the repo cannot hold the step's rules without depending on omp
     or dynaflows (`profile-check` checks the record itself), or it has no
     checker capability yet (a greenfield repo).
   Harness-owned per-edit `path-check` is outside this delegation: it always runs with the
   required profile settings, even where repo checks overlap. See
   [ADR 0002](../../../../../../docs/adr/0002-harness-owns-path-checks.md), the principles 1–2 exception.

   `SECURITY.dependency-audit` has no check step and never the `harness`
   outcome, even in a greenfield repo: any repo can hold an audit without the
   harness. It is `repo` when a checker that `checks.commit` and `checks.ci`
   run meets the catalogue row's contract for every lock file and manifest in
   Step 1's map. Otherwise it is `grow`: each uncovered lock file, unreadable
   lock format, unlocked manifest, staged-file or scheduled trigger, severity
   filter, waiver or off-format finding line is fixed in the repo's checker,
   and a repo with no audit gets one. The repo's exception file goes into the
   record's `ownerOnlyPaths`.

   Never run a harness step beside a repo checker, even until a repo PR lands,
   and never offer a route where the owner declines a `grow` PR and the harness
   step runs instead. A rule that does not apply needs Step 1 evidence that its
   construct is absent and the owner's approval. `guardrails`' own tests in
   oh-mi-pi-config prove the harness rules once for every repository; the
   repo's tests prove the repo's checkers. Read source-specific roots and
   allowlists from target data rather than hard-coding them.
7. The same baseline rule covers the repo's own tools for catalogue rules
   without a `reference` (Ruff, mypy and similar): a baseline of waived
   findings is a `grow` fix, never an accepted state. mypy follows every
   import (`MYPY.follow_imports`): first-party errors are reported, installed
   third-party packages stay silent, and each vendored third-party module
   found in Step 1 gets a named `follow_imports = silent` override.
   Third-party types come from upstream: the package's own annotations, with a
   named `follow_untyped_imports = true` override for a package without
   `py.typed`, or a published stub package such as `types-PyYAML`. Use
   `ignore_missing_imports` only for a package with neither. Every Step 1
   boundary finding is a `grow` fix under design directive principle 10:
   delete in-repo stubs, copies and shims of third-party code and any checker
   exemption for them; scope a checker that reaches third-party code to owned
   files; move third-party imports and upstream workarounds into the owned
   adapter; make tests replace the owned interface. Where an applicable rule
   has neither a repo home nor an `guardrails` subcommand, the
   implementation is a repo checker when the repo has the capability, and
   otherwise a change to `guardrails` in oh-mi-pi-config with its own
   tests; keep the rule blocked until that change lands. Record deviations as
   unresolved decisions, not passes. Reuse existing ownership/naming/runner
   policies rather than adding parallel authorities.

## Step 3: engine candidates

1. Draft the record from the harness; author an adapter if the framework has
   none. Where the stack has a skill reference (Python:
   [`guardrails`](../../../engine/README.md)), never copy,
   adapt or edit its code, and never install it into the target's environment
   or lockfile. Run it from the harness against a temporary clone of the
   target under the candidate root: `uv run --project <omp
   root>/agent/extensions/dynaflows/engine
   guardrails init --repo <temporary clone> --record <draft record dir>`.
   `<draft record dir>` sits under the candidate root, never under the records
   directory; in extend mode, start it as a copy of the existing record.
   `init` discovers the repo's make targets, hook managers, CI jobs and
   (through `gh`) required checks, and drafts the record's `gate.json` and
   `profile.md` from them. It writes nothing into the clone, never overwrites
   a recorded value, never writes an approval and never turns dynaflows on, so
   a second run changes nothing. It writes proposed native-tool configs to the
   record's `proposed/` directory for the owner to apply to the repo as a fix,
   and proves language-server readiness with a canary `references` query.
   `--commit-command` and `--ci-command` name the repo's own gate commands;
   `--runner` and `--required-context` set the record's `ci` object.
   Init reports a missing `checks.commit` as `required:`, because a record without it
   does not load. There is no path-command option or record field. In adopt mode,
   shadow-run each `repo` or `grow` step on the same clone: `guardrails
   <step> --repo <temporary clone> --record <draft record dir>` and the
   record's `checks.commit`. Sort every finding the harness reports and
   `checks.commit` passes:
   - the repo checker lacks the rule, scopes it narrower, baselines it or
     misses files: add the fix to the step's `grow` PR;
   - the repo checker's own exception or setting accepts code that a stale
     record setting rejects: the repo checker is the authority, so drop the
     record setting;
   - never change repo code only to satisfy the harness copy.

   Zero findings on both sides shows no gap but not coverage; coverage comes
   from Step 2's rule-ID evidence. Draft each `grow` PR as candidate repo
   changes, with tests for every added rule. Give `repo` and `grow` steps to
   the repo in the draft record with `guardrails init --settings`
   (`check` itself takes no options), for example `{"check": {"repo":
   {"python-quality": ["scripts/checks/check-python-quality.py"]}}}`, and leave
   out settings only owned steps read (`check` rejects `guardrails.python`
   once every configured Python step is the repo's). Then fill every per-repo
   value in the front matter of the draft `profile.md`: tiers, layout,
   `structure`, scope exclusions, `guardrails.exceptions` (each with an owner-approved reason)
   and `guardrails.<checker>` settings. A behaviour the settings cannot
   express is a change to the engine itself, never a local fork. If the
   target's test framework has no adapter, follow [adapter
   authoring](adapter-authoring.md). For a stack without a reference, the
   missing checkers are a change to the harness, not to the target (Step 2).
2. Draft required profile `guardrails.structure`, `guardrails.naming` and `guardrails.paths`
`uv run --project` with `agent/extensions/dynaflows/engine/`. It covers the whole repo
   It covers `STRUCT.dependency-direction`, `STRUCT.folder-placement`, `NAME.conventions`
   and `TEST.placement`. Rails supply changed paths as separate arguments; clean exits 0,
   failures exit nonzero with one error per line naming the file, rule ID and concrete fix.
   Do not add a repo path-check script for the harness or remove settings because whole-repo
   checks are repo-owned. Preserve the repo's own commit and CI commands.
3. The test-evidence checks read normalised evidence (the adapter's JUnit run
   report and per-tier collect-only test IDs), not console text. The harness
   runs `guardrails report-check` and `collection-check`, and
   `budget-check` at the agent's commit only, with JUnit and temporary output
   outside the worktree. Budgets select approved per-machine limits from the
   record's `profile.md` by a deterministic local machine probe, block
   overruns at the agent's commit, treat an unknown machine as report-only
   with a prompt to record a baseline, and fail a missing, invalid or stale
   report. CI never measures or reports test timing. Name overrunning tests
   with a speed-up fix; never propose a tier move because a test is slow.
4. `guardrails diff-check` implements `TEST.growth.justified` and
   `TEST.duplicate-path`. It requires a descriptive behavior name per new
   test function (Python function name or JS/TS test title); parameter rows
   share it. It checks presence with at least two non-generic alphabetic words,
   and rejects duplicates by normalised call-path
   and assertion fingerprints, naming both cases and the merge-or-remove fix.
   The remaining test-guidance rules follow each catalogue row's `reference`:
   `diff-check` (coverage ratchet, golden change record, identity artefacts,
   timeout increases, test growth and duplicate paths), `python-test-hygiene`
   (subprocess timeouts), `guard-run` (user-data guard, fast-tier isolation),
   `surface-lint` and `lever-selftest` (verification surface),
   `profile-check` (`PROFILE.complete`), `adapter` (source patterns for
   self-mocking, test hooks, evasion markers and property seeds) and, in T3
   only, `mutation-check` after the adapter's `mutation-report`. Affected T2
   selection uses `guardrails adapter affected --base <ref>`, which reads
   the record's Ownership areas. These run from the harness; none is wired
   into the target.
5. Draft the repo-side controls the project wants without the harness: a
   check-only full runner for `GATE.local-ci-parity` with tier selectors, and
   CI wiring that runs `checks.ci` (full T1 and T2) on every push and pull
   request, either as one job named by the record's `ci.requiredContext`
   (default `gate`) or as parallel jobs that call the same runner with tier
   selectors whose union equals `checks.ci`. Parallel CI must include an
   aggregate job and status named by `ci.requiredContext`, depending on every
   tier job. It must run even after dependency failure (for GitHub Actions,
   `if: ${{ always() }}`) and succeed only if every tier's result is
   `success`; failed, cancelled, or skipped tiers must make the required
   context fail. A commit target (`checks.commit`: static checks, every T1,
   affected T2 boundary tests) serves `GATE.pre-commit`; the repo may run it
   from its own hook manager. Do not add a pre-push hook; remove an existing
   one by the approved decision. None of these files names omp, dynaflows or
   the record.
6. Prepare candidate fixes for every Step 4 finding in the temporary clone.
   Never weaken the real repository's protection to clear a finding; a scope
   exclusion covers only generated, vendored or fixture paths. Draft
   contributor commands separately from application-user guidance. Keep all
   candidates and the draft record outside the target and outside the
   records directory until Step 5 approval; identify the superseded
   checks/config to remove, including any omp-specific files the repo holds.
   History is exempt: never rewrite an ADR or a frozen, digest-pinned study or
   evaluation record to remove an omp mention.

## Step 4: engine dry run

1. Run the harness checks from the harness against a temporary checkout with the draft record:
   `uv run --project <omp root>/agent/extensions/dynaflows/engine
   guardrails check --repo <temporary checkout> --record <draft record dir>`.
   They read nothing from, and write nothing into, the checkout's tree. Count findings by rule ID
   and path over the whole repository. Steps the draft record's `guardrails.check.repo` gives
   to repo checkers run through `checks.commit` instead; report that command's result with
   them. Mark an estimate `unknown` where safe execution or a
   candidate checker is unavailable.
`uv run --project` with `agent/extensions/dynaflows/engine/`. It covers the whole repo
   with candidate changed paths. It always uses required profile `guardrails.structure`,
   `guardrails.naming` and `guardrails.paths`, independently of `guardrails.check.repo`;
   report its findings separately.
2. Report one view: the whole repository minus the draft record's scope exclusions. Adopting a
   repository means clearing every finding first; there is no baseline and no new/changed-code
   view. A narrower rollout changes which rules are enabled, never which paths a rule scans.

## Step 5: approval and proposal

1. Present an action matrix: ID, rule IDs, current owner, proposed owner (repo control or
   harness check), audience, operation (`keep`, `merge`, `replace`, `add`, or `defer`), paths,
   dry-run change count, findings, verification, and risk. `keep` gives a `repo` step to its
   checkers (`guardrails.check.repo`), `merge` or `replace` is the `grow` PR that changes the
   repo's checkers before they own the step, and `add` runs a `harness` step. No row runs a
   harness step for a rule a repo checker holds or will hold.
2. Present the draft record for confirmation: every `gate.json` command and its discovered
   source (make target, hook entry, CI job, required check), `ci.requiredContext`, `ci.runner`,
   `ownerOnlyPaths` (the dependency-audit exception file and any other path only the owner
`uv run --project` with `agent/extensions/dynaflows/engine/`. It covers the whole repo
   or exception, including whether a current hook, CI workflow, config, or developer document
   would be removed or changed.
3. For existing repositories, request an explicit operation for each conflicting practice. For a
   greenfield repository, propose a minimal central runner and only the stack-relevant rules; do
   not invent a CI provider.
4. Show the complete applicable rule mapping from Step 2 and the whole-repo findings from Step 4.
   A rule still blocked by a missing harness implementation stays blocked; do not offer a
   passing approval path by relabeling it.
5. Offer two tiers. `Minimal`: least change that gives the record the required
   `checks.commit` naming the repo's own command and required profile `guardrails.structure`,
   `guardrails.naming` and `guardrails.paths`, plus repo fixes that clear every finding of the
`uv run --project` with `agent/extensions/dynaflows/engine/`. It covers the whole repo
   recommendation, including `checks.ci` wired into the repo's CI by the record's `ci.runner`
   role. In `adopt` mode both tiers give every `repo` and `grow` step to the repo's checkers in
   `guardrails.check.repo` and include every `grow` PR; a tier never keeps a duplicate harness
   step, or widens record allowlists so the harness copy passes code the repo checker already
   accepts.
   Write the repo-relative candidate paths per tier to `guardrail-proposal.json` in the draft
   record directory as `{"schemaVersion":1,"minimal":[...],"full":[...]}`; it goes into the record
   with the rest of it. Nothing is written into the target, and nothing into the live record before
   the owner's approval.
6. Ask the owner to choose a tier, confirm the record, and approve exact candidate paths and
   merge/replace decisions, using `ask` with a question containing "guardrail" and options "Minimal"
   and "Full". When both tiers hold the same record settings and candidate paths, say so and ask
   only for approval of that one draft, with options "Approve" and "Revise". Write neither the
   record's `gate.json` nor any proposal path until this is answered. **STOP** until the owner
   replies; elapsed time or a default option is not approval.
7. If the owner chooses a narrower rule set, recompute the candidate diff, findings and
   proposal file while preserving whole-repo scope for every rule it keeps.

## Step 6: authority cutover

1. Apply only approved paths. Merge surgically or replace only by the owner's
   decision. Remove approved superseded runner/hook policies and any
   omp-specific files instead of retaining duplicate authorities; where
   nothing can be removed, record why the added contract has no predecessor.
   A repo checker named in `guardrails.check.repo` is the authority for its
   step; never remove it in favour of the harness step. The approved record
   is saved before the `grow` PRs are committed, so stale harness
   settings never block the commits that replace them. It gives every step
   whose checker files the repo already lists to the repo. A `grow` step whose
   checker the PR creates stays a harness step until that PR merges, because
   `check` rejects a checker path the repo does not list; the owner then moves
   it to `guardrails.check.repo`. Setup is not done until every `grow` PR has
   merged with green checks and the record names every repo checker.
   Repo changes land through a normal PR and never name omp, dynaflows or the
   record.
2. Save the confirmed record as `repos/<owner>/<name>/` under the
   oh-mi-pi-config root (Step 0's path). Write the live record yourself; never
   ask the owner to save a file.
   `/repos/` is gitignored: never commit a record, stage it or copy it into
   the target. The device rollout copies records to every device. Where the
   stack has a skill reference, run `guardrails init --repo <worktree>
   --record <draft dir>` from the harness, then write the confirmed profile
   values into the draft; compare it with the approved matrix before handing
   it to the owner. Install no harness
   package into the target. Pin tools the repo itself runs through the target
   package manager. Keep target roots, scope exclusions, allowlists,
   naming/ownership maps, and CI wiring explicit.

## Step 6: execution events

   1. Commit: rails run harness `guardrails path-check --repo <worktree> --record <record dir>`,
      then the record's `checks.commit` (static checks, every T1 test, and affected T2
      boundary tests: seams and journeys) and then the harness checks over the
      whole repo. Any finding
      blocks the commit until fixed, including drift from contributors who do
      not use omp. No baseline and no diff scoping apply.
   2. Push and pull request: the repo's CI runs `checks.ci`, the full T1 and T2
      suite.
   3. Merge: the declared required context (`ci.requiredContext`, normally
      `gate`) from `checks.ci` must be green on the exact head, whoever posts
      it; with dynaflows on, `prove()` must also have passed on that head.
   4. Schedule: T3 runs on a schedule, outside the record's checks.

   No pre-push hook and no handoff check run exist. CI runs `checks.ci` as one
   job named by `ci.requiredContext` or as parallel tier jobs of the same runner
   whose union equals it. Parallel CI requires an aggregate job/status named by
   `ci.requiredContext` depending on every tier job. Run the aggregate
   unconditionally after dependencies finish, including on failure, and fail
   unless every tier result is `success` (not failed, cancelled, or skipped).
   After edits, rails also run harness `guardrails path-check` with the repo and record
   arguments above, appending changed paths as separate arguments. It exits 0 when clean;
   failures name each file, rule and concrete fix. Rails use one fixed timeout.
   Keep required profile path-check settings even where whole-repo checks are repo-owned.
   Preserve the repo's existing commit and CI commands; no harness path script belongs in the repo.

## Step 6: hook budgets and host policy

1. A repo hook is the repo's own control. When the project wants one, run
   `checks.commit` through the repo's tracked hook manager or a native
   `.githooks/pre-commit` enabled with `git config core.hooksPath .githooks`,
   propagating nonzero status. Budget timing blocks only at the agent's commit,
   using the record's approved machine limit; `checks.ci` never runs the
   budget checker, since CI never measures or reports test timing. Missing,
   invalid or stale reports fail. First limits and raises need owner approval;
   unapproved machines report only and prompt for a baseline. Keep auto-fix
   explicit and outside check-only CI.
2. Do not configure host branch protection. Rails check that
   `ci.requiredContext` is green on the exact head before merge; a server-side
   ruleset is a later, optional owner step outside this skill.

## Step 7: engine and integration verification

1. Run the harness checks from the harness over the whole repository with the
   record, as rails do at commit: `uv run --project
   <omp root>/agent/extensions/dynaflows/engine
   guardrails check --repo <worktree> --record <record dir>`. Expected exit
   0: zero findings outside the record's
   scope exclusions, with JUnit and temporary output outside the worktree and
   no file written into it. `guardrails`' own tests in oh-mi-pi-config
   prove the checkers once for every repository.
2. For each repo-owned checker the project keeps for itself, including every
   checker named in `guardrails.check.repo`, run its own tests; `check`
`uv run --project` with `agent/extensions/dynaflows/engine/`. It covers the whole repo
   `NAME.conventions` and `TEST.placement` implemented by the repo's own path
   command, assert that each failure names the file and exact corrective
   action.
3. Prove the record's test-evidence wiring: the harness runs the budget
   checker at the agent's commit and blocks on its failure, and `checks.ci`
   never runs it. Prove machine selection against the record's approved
   limits; an unapproved machine prompts for a baseline and reports timing
   only.
4. Parse the record's `gate.json` and verify Step 6's schema, version, field
   types, command arrays, globs, merge-policy enum/defaults, `ownerOnlyPaths`
   and a boolean `dynaflows.enabled`. Optional fields must remain optional to the format
   validator. Prove the target's own wiring: actual CI runs `checks.ci`; for
   parallel CI, the aggregate depends on every tier and fails on any failed,
   cancelled, or skipped result, not just that tier selectors cover
   `checks.ci`. In an isolated checkout, a passing `checks.commit` with zero
   harness findings permits an agent commit, and a failing one blocks it.
   Never test by pushing to a protected branch.
5. Verify tool selectors, limits, exceptions, and pinned versions. Run
   `checks.commit`, `checks.ci` (or its CI tier jobs), and local/CI parity
   tests. Do not count skipped, unavailable, or timed-out checks as passes.
   Verify actual CI on the final checked commit when approved.
6. Confirm `git status` in the target shows no record, proposal, JUnit or
   other harness output, and that `git grep -n -i -w -E 'omp|guardrails|dynaflows'`
   finds no harness mention the change added. Rerun the harness checks after repairs;
   compare final paths with the approved dry-run matrix. Confirm superseded
   contracts were removed or record why nothing could be removed. Return the
   specified terminal format with rule IDs, commands/results, change counts,
   documentation changes, and blockers. Do not save a retained summary
   artifact.
