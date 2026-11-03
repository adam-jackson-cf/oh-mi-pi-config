---
question: >-
  Does this reply announce that the assistant is replacing, renaming or migrating existing code
  now or next, as its own work? Answer no for instructions to another agent, reviews, questions,
  or comparisons of options.
condition: ['(?i)\b(?:replac\w+|renam\w+|migrat\w+|cutover|shims?|compat(?:ibility)?|backwards?[- ]compat\w*|deprecat\w+|legacy|aliases|alias|re-?exports?)\b']
scope: text
---
# Clean cutover

Read skill://coding-guidance/references/clean-cutovers.md before changing the existing design.
