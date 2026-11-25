# Task briefs

## Objective

List the reads and context every proof, implementation and sweep task receives before editing.

## Guidance

- Every brief requires its reads before the first edit, and includes the worktree, criteria, manifest, scope and
  task-specific references.
- Proof briefs supply the record's `profile.md` path, [proofs](proofs.md), and
  `skill://test-guidance/assets/templates/installed/references/writing-tests.md`. Add `property-tests.md`, `golden-files.md` and `boundary-tests.md` under that
  prefix when applicable.
- Proofs use outcome tests or lever journeys. Freeze required lever changes with runner-invoked journey files.
- Implementation briefs supply [delivery principles](delivery-principles.md), applicable
  [structure and naming](structure-and-naming.md), [proofs](proofs.md),
  `skill://test-guidance/assets/templates/installed/SKILL.md#every-task`, `skill://test-guidance/assets/templates/installed/references/writing-tests.md`
  and `skill://test-guidance/assets/templates/installed/references/boundary-tests.md` (map entries and discovery tests),
  plus `skill://git-tool`, `skill://git-tool/references/branch-and-integrate.md`
  and `pr-description.md` under that prefix.
- Implementation changes product code and feature-map docs, never frozen lever or proof bytes;
  a new proof comes only through a discovery re-freeze.
- Sweep briefs supply the base and head, the record's feature-map path, [proofs](proofs.md),
  `skill://test-guidance/assets/templates/installed/references/boundary-tests.md` and `writing-tests.md` under that prefix.
