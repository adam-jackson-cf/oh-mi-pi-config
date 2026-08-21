# Freeze the protocol

## Objective

Make every experimental input immutable before baseline execution.

## Required actions

1. Freeze fixtures, prompts, hidden tests, rubric, model, thinking level, timeouts, permissions, and run counts.
2. Write protocol.json, manifest.json, and checksums.json.
3. Validate baseline and candidate differ only by the exact instruction treatment.
4. Require a new run for any post-freeze design change.

## Done when

- Every required action has evidence in the active run directory.
- No unresolved validation error remains before the next workflow step.
