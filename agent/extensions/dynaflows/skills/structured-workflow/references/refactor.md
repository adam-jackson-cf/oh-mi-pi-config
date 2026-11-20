# Refactor playbook

## Objective

Make the target design canonical, migrate every caller and preserve required behaviour.

## Stage deltas

- Frame: inventory callers and preserved behaviours. Read [delivery principles](delivery-principles.md).
- Spike: resolve design uncertainty only when inspection cannot settle it.
- Plan: a behaviour-preserving refactor maps no proofs and freezes an empty manifest; the repo's existing tests and CI
  carry the preserved behaviour.
- Plan: `prove()` blocks any feature-map doc changed without a linked proof, so a refactor that moves a mapped file and
  must edit a map link is not zero-proof: raise it with the owner in Plan.
- Plan: a part of the refactor that adds behaviour is a feature and gets proofs as in [Feature](feature.md).
- Implement: main freezes the empty manifest, or completes the proof task and red-at-base freeze for added behaviour,
  before the implementation task migrates callers, tests and docs.
- Implement: remove obsolete implementations, aliases, comments and compatibility paths.
