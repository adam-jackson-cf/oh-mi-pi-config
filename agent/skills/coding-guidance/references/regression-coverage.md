# Regression coverage

## Objective

Identify and flag the failing behaviour, instead of pinning the one scenario that exposed it.

## Guidance

- Before fixing, name the failing behaviour: the rule the code broke, stated beyond the one input that exposed it.
- Check whether existing behavioral tests cover that rule, and flag in the result the failing behaviour and whether coverage protected it.
- When coverage of that rule is missing, expand the relevant existing behavioral scenario
  so it protects the intended outcome rather than the incidental failing value.
  When no behavioral scenario covers the area, flag the missing coverage in the result instead of adding a test for the fixed case.
- Never add a regression test that only proves one bug-fix value, example, or changed text does not change back.
- Never add duplicate or narrowly tailored regression tests that encode only the discovered example.
