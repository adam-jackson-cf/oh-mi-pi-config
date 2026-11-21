# Task briefs

## Objective

List the reads and context every proof, implementation and sweep task receives before editing.

## Guidance

- Every brief requires its reads before the first edit, and includes the worktree, criteria, manifest, scope and
  task-specific references.
- Proof briefs supply the record's `profile.md` path, [proofs](proofs.md), and
  `skill://test-guidance/references/case-selection.md`, `naming-and-layout.md`, `outcome-tests.md` and `tiers.md`
  under that prefix. Add `property-tests.md`, `determinism.md`, `behaviour-and-statistics.md` and
  `verification-surface.md` when applicable.
- Proofs use outcome tests or lever journeys. Freeze required lever changes with runner-invoked journey files.
- Implementation briefs supply [delivery principles](delivery-principles.md), applicable
  [structure and naming](structure-and-naming.md), [proofs](proofs.md),
  `skill://test-guidance/references/before-commit.md`, `verification-surface.md` and `outcome-tests.md`
  (map entries and discovery tests), plus `skill://git-tool`, `skill://git-tool/references/branch-and-integrate.md`
  and `pr-description.md` under that prefix.
- Implementation changes product code and feature-map docs, never frozen lever or proof bytes;
  a new proof comes only through a discovery re-freeze.
- Sweep briefs supply the base and head, the record's feature-map path, [proofs](proofs.md),
  `skill://test-guidance/references/verification-surface.md` and `outcome-tests.md` under that prefix.
