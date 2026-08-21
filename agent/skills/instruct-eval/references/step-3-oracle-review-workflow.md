# Review semantic oracles and rubric

## Objective

Prove tests and scoring distinguish behavior without encoding unspecified implementation details.

## Required actions

1. Run pristine, complete-reference, and plausible-failure probes.
2. Require public and hidden tests to assert observable contracts only.
3. Define exact preferred and non-preferred architecture archetypes and rubric dimensions.
4. Stop on parameter-order, source-text, timing, or implementation-specific oracle assumptions.

## Done when

- Every required action has evidence in the active run directory.
- No unresolved validation error remains before the next workflow step.
