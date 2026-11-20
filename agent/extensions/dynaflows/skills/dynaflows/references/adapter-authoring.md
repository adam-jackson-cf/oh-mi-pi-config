# Adapter authoring

## Objective

Give a test framework that has no adapter one, accepted by a deterministic
conformance run rather than by review.

## When this applies

The harness setup's Step 3 reaches this reference when the target's test framework has no adapter
in the engine. The shared test-evidence checkers stay
framework-agnostic: they read only normalised evidence. An adapter is the thin
framework-specific part that produces that evidence.

## Interface

An adapter does only these six things. No framework is assumed.

1. **Run report.** Emit a JUnit XML report for every tier run.
2. **Tier listing.** List the collected test IDs per tier, without running
   them, using the adapter's selectors in the profile's `adapters` entry. Also collect test cases
   statically from source (`cases.collect(path, text)`): each case's test ID
   with a fingerprint of its normalised calls and assertions, and each test
   function's descriptive behavior name (Python function name or JS/TS test
   title). The shared
   `guardrails check` compares HEAD and the staged changes with it at commit, and
   `guardrails diff-check` compares base and head, for
   `TEST.growth.justified` and `TEST.duplicate-path`.
3. **Fast-tier guard.** Provide the hook that blocks process creation and
   network connections across T1 setup, call and teardown
   (`TEST.fast-tier-isolation`). CI also denies the network at the container;
   the hook is the local mechanism.
4. **Source patterns.** Supply `ast-grep` YAML rules for the framework's mock,
   patch, skip, focus, retry and seed idioms (`TEST.no-self-mocking`,
   `TEST.no-test-hooks`, `TEST.no-evasion`, `TEST.property-seed`). "Owned code"
   means the packages owning the profile's `public_entry_points`. For
   `TEST.no-test-hooks`, report production reads of test markers (environment
   reads and loaded-module or runner-global checks), never marker-shaped strings
   held as data, and also report production symbols that only tests use. Each
   pattern carries a `remediation` with its root cause and a holistic fix.
5. **Mutation tool.** Name the framework's mutation tool, pin it, and convert
   its results into the shared mutation report (mutant, file, line span,
   status). `guardrails mutation-check` judges that report against the diff
   (`TEST.mutation-sensitivity`, a T3 nightly rule).
6. **Normalisation.** Convert the framework's JUnit dialect (durations, skip
   reasons, retry markers, empty-collection exit codes) into the shared run
   report schema. Dialects differ, so normalisation lives in the adapter, never
   in a shared checker.

An adapter belongs to a test framework, not a tier. The record's `profile.md` lists one
`adapters` entry per framework the repo uses; each names its adapter and gives, per tier it
takes part in, the framework's native `selector` and the `location` handed to it. Every tier
may hold tests from several frameworks, and `guardrails adapter <name> <action>` runs one
adapter. Per-repo values go in the record, never in adapter code.

## Conformance spec

The conformance cases are framework-agnostic: each names an input situation and
the verdict every adapter must produce. The canonical list is the pytest
adapter's
[`conformance/cases.json`](../../../engine/guardrails/commands/adapters/pytest/conformance/cases.json).
It includes, for example:

- a test patches an owned module → `TEST.no-self-mocking`;
- an unapproved skip → fail (`TEST.no-evasion`);
- a test writes under `HOME` → fail (`TEST.user-data-guard`);
- an empty collection → fail (`TEST.failure-propagation`);
- a T1 test opens a socket or starts a process → `TEST.fast-tier-isolation`;
- a property test without a set or reported seed → `TEST.property-seed`.

1. Write every case as a fixture test in the target framework, with its
   expected verdict. The fixtures are harness tests beside the adapter, never
   files in the target repository.
2. Freeze the fixtures before writing the adapter, as DynaFlows freezes proofs:
   the adapter changes, the fixtures do not.
3. Run the shared checkers over the adapter's evidence for each fixture. The
   adapter is accepted only when every case produces its expected verdict.
   A case the framework cannot express is a blocker for the owner, never a
   silent skip.

The shared checkers ship synthetic JUnit XML fixtures, so their own tests never
depend on any framework.

## Where the adapter lives

- The adapter lives in the harness from its first repo: under
  `agent/extensions/dynaflows/engine/guardrails/commands/adapters/<framework>/`
  in oh-mi-pi-config, with conformance fixtures as the engine's own tests.
  The harness owns the adapter; the target repository has no awareness of it.
- The harness runs the adapter against the target from outside its tree; JUnit
  and other evidence go to a temporary directory outside the worktree.
- **Generalisation (rule of two):** keep framework-specific choices that only
  the first repo needs in that repo's record settings. Generalise the adapter's
  code only when a second repo uses the same framework, and only once it still
  passes every conformance case.

## Ladder

These authoring instructions are prose for the author. Acceptance is
deterministic: the conformance run passes or fails.

## Done when

- The adapter implements all six interface items and reads per-repo values from
  the record's `profile.md`.
- Every conformance case is a frozen fixture, and the conformance run passes.
