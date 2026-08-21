# Define treatment and causal hypothesis

## Objective

Make the supplied instruction the only intentional difference between baseline and candidate conditions.

## Required actions

1. Preserve the instruction verbatim.
2. Define the desired behavior, competing behavior, candidate injection point, and falsifiable treatment hypothesis.
3. Reject task prompts that directly reveal the desired architecture.
4. Pre-register run counts, practical-effect threshold, and stop conditions.

## Done when

- `protocol.json` contains a validated `design_review` object recording the desired behavior, competing behavior, candidate injection point, falsifiable hypothesis, and approved architecture-leak review.
- No unresolved validation error remains before the next workflow step.
