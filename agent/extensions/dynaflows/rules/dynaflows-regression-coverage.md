---
agents: [main, task]
repeatMode: once
question: >-
  Is this reply announcing or making a change to correct incorrect runtime behaviour (a bug
  or regression)? Answer no for type, compile, lint or formatting errors, new features,
  refactors, reviews and plans.
condition: ['(?i)\b(?:bugs?|regression|root cause|repro(?:duce[sd]?|duction)?|defect|off-by-one|race condition|crash(?:es|ed|ing)?|incorrect(?:ly)?|wrong (?:result|output|value|behaviou?r))\b']
scope: text
---
# Regression coverage

Read skill://dynaflows/references/delivery-principles.md and
skill://test-guidance/references/case-selection.md before implementing the fix.
