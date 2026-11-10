# Adapter authoring

## Objective

Give a test framework that has no adapter one, accepted by a deterministic
conformance run rather than by review.

## When this applies

Step 3 reaches this reference when the target's test framework has no adapter
in the stack's skill reference. The shared test-evidence checkers stay
framework-agnostic: they read only normalised evidence. An adapter is the thin
framework-specific part that produces that evidence.

## Interface

An adapter does only these six things. No framework is assumed.

1. **Run report.** Emit a JUnit XML report for every tier run.
2. **Tier listing.** List the collected test IDs per tier, without running
   them, using the selectors in the profile's `tiers`.
3. **Fast-tier guard.** Provide the hook that blocks process creation and
   network connections across T1 setup, call and teardown
   (`TEST.fast-tier-isolation`). CI also denies the network at the container;
   the hook is the local mechanism.
4. **Source patterns.** Supply `ast-grep` YAML rules for the framework's mock,
   patch, skip, focus, retry and seed idioms (`TEST.no-self-mocking`,
   `TEST.no-test-hooks`, `TEST.no-evasion`, `TEST.property-seed`). "Owned code"
   means the packages owning the profile's `public_entry_points`.
5. **Mutation tool.** Name the framework's mutation tool and the command that
   scopes it to changed code (`TEST.mutation-sensitivity`, a T3 nightly rule).
6. **Normalisation.** Convert the framework's JUnit dialect (durations, skip
   reasons, retry markers, empty-collection exit codes) into the shared run
   report schema. Dialects differ, so normalisation lives in the adapter, never
   in a shared checker.

The repo test profile names the adapter in `adapter` and each tier's selector
in `tiers`. Per-repo values go in the profile, never in adapter code.

## Conformance spec

The conformance cases are framework-agnostic: each names an input situation and
the verdict every adapter must produce. The canonical list is the pytest
adapter's
[`conformance/cases.json`](../assets/python-reference/omp_guardrails/adapters/pytest/conformance/cases.json).
It includes, for example:

- a test patches an owned module → `TEST.no-self-mocking`;
- an unapproved skip → fail (`TEST.no-evasion`);
- a test writes under `HOME` → fail (`TEST.user-data-guard`);
- an empty collection → fail (`TEST.failure-propagation`);
- a T1 test opens a socket or starts a process → `TEST.fast-tier-isolation`;
- a property test without a set or reported seed → `TEST.property-seed`.

1. Write every case as a fixture test in the target framework, with its
   expected verdict.
2. Freeze the fixtures before writing the adapter, as DynaFlows freezes proofs:
   the adapter changes, the fixtures do not.
3. Run the shared checkers over the adapter's evidence for each fixture. The
   adapter is accepted only when every case produces its expected verdict.
   A case the framework cannot express is a blocker for the owner, never a
   silent skip.

The shared checkers ship synthetic JUnit XML fixtures, so their own tests never
depend on any framework.

## Where the adapter lives

- **First repo:** the adapter is repo-local, beside the repo's other test
  support, and the repo's coverage ledger proves its rules like any checker.
- **Promotion (rule of two):** when a second repo uses the same framework, the
  adapter moves into the stack's skill reference under `adapters/<framework>/`,
  only once it passes every conformance case. The first repo then pins the
  reference and deletes its local copy in the same change.

## Ladder

These authoring instructions are prose for the author. Acceptance is
deterministic: the conformance run passes or fails.

## Done when

- The adapter implements all six interface items and reads per-repo values from
  the profile.
- Every conformance case is a frozen fixture, and the conformance run passes.
