# Preflight experiment workspace

## Objective

Resolve the repository root, validate OMP capabilities, and allocate an isolated run under .experiments/instruct-eval.

## Required actions

1. Parse the exact instruction and optional --thinking argument, defaulting to medium.
2. Validate provider, model, thinking level, OMP version, Python version, and writable repository root.
3. Create a unique run directory without modifying .todo or activating candidate guidance.
4. Record resolved arguments and tool versions in request.json.

## Done when

- Every required action has evidence in the active run directory.
- No unresolved validation error remains before the next workflow step.
