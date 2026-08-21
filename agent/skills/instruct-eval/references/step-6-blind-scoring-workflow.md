# Blind-score architecture

## Objective

Measure design outcomes without revealing condition identity.

## Required actions

1. Create a randomized alias map after initial trials finish.
2. Copy each implementation into a clean scoring workspace without condition metadata.
3. Run the frozen scorer prompt and architecture rubric.
4. Validate score schema, scorer exit status, and absence of production edits.

## Done when

- Every required action has evidence in the active run directory.
- No unresolved validation error remains before the next workflow step.
