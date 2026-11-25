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

Repair the failure class, not the reported value.

Before the fix:

- Record the actual and expected behaviour, and the criteria that make an input a member of the
  failing class.
- Reproduce the failure first. For an intermittent failure, follow the flaky-test rule.
- Name the broken rule beyond the one input that exposed it.

The fix:

- Repair the root cause, not a special case of the reported input.
- Never suppress, default or work around the failure, and never loosen a gate or a test to pass.

Coverage:

- Check whether existing behavioural tests cover the broken rule. Report the failing behaviour and
  whether coverage protected it.
- Cover representative members of the failure class, not only the reported input.
- When coverage is missing, extend the relevant existing scenario so it protects the intended
  outcome, not the incidental value.
- If no scenario covers the area, add one outcome case for the intended behaviour, shown failing
  before the fix, and report the coverage gap.
- For a pure-core bug, add a property over the failure class. Add an outcome case only for caller
  behaviour that the property or an existing journey does not cover.
- Never add a test that pins only the fixed value, one example or changed text, and never
  duplicate an existing case.
- Keep a new case only when it has a reachable user path, a caller-visible outcome, an outcome
  class no other case asserts and a cost that fits its tier. Otherwise extend an existing
  scenario, or add no test.
- Report the reproduction, the root cause and the covering tests at handoff.
