# Execute trials and checkpoints

## Objective

Capture fresh first-pass baseline, candidate, and subsequent change-pressure results.

## Required actions

1. Run unique disposable fixture copies in fresh OMP processes.
2. Use the same resolved --thinking value for every condition and scorer process.
3. Preserve commands, outputs, exit codes, versions, context checksums, diffs, and test outcomes.
4. Restore baseline context before applying identical checkpoint requests to every initial workspace.
5. Never count corrective turns.

## Done when

- Every required action has evidence in the active run directory.
- No unresolved validation error remains before the next workflow step.
