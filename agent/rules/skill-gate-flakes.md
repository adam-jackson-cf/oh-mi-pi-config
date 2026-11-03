---
agents: [main, task, sonic, designer]
question: >-
  Is this reply diagnosing, retrying, quarantining or fixing a test that fails only some of
  the time (flaky, intermittent or order-dependent)? Answer no for deterministic failures,
  aborted processes and non-test commands.
condition: ['(?i)\b(?:flak(?:e|y|iness)|intermittent(?:ly)?|non-?determin\w+|sporadic(?:ally)?|(?:fails?|failed) (?:sometimes|occasionally|randomly)|passes? on (?:re-?run|retry))\b']
scope: text
---
# Flaky test

Read skill://test-guidance/references/flakes.md before handling the intermittent failure.
