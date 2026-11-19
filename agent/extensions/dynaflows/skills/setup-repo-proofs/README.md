# Repository guardrail setup

Use this workflow to establish or extend enforceable controls for any language/runtime stack.
It inventories existing practice, maps a selected rule contract to the project's own tools,
measures whole-repository impact without target edits, and applies only approved changes.
A rule is enforced only after its implementation, invocation and outcome tests succeed.

## When to use it

- A new Git repository needs developer guardrails.
- An existing repository needs complete coverage without replacing working lint, test or CI practice.
- Application-user guidance and contributor guidance need distinct owners.

## Getting started

Read [the workflow](SKILL.md), select the project's authoritative rule contract, and follow Steps
0–7 in order. Existing controls retain ownership: add missing rules to them, remove superseded
authorities only with approval, and keep build, tests, checks, hooks and CI independently runnable.

Example requests:

- "Assess this Python repository and show the complete guardrail dry run before editing."
- "Set up guardrails for this TypeScript repository and list every finding to clear first."
- "Merge equivalent checks with existing CI; show rules that cannot be enforced."

## Completion

Every applicable rule has an effective check and outcome tests. Whole-repository findings,
local/CI parity, exact approved paths and unresolved blockers are visible in the terminal result.
Nothing is applied before the owner's explicit approval.
