# Route decisions

## Objective

Settle what a route over options asks, falls back to and counts as correct, so framing, labels and
tuning treat it the same way.

## Guidance

- Use this file when the [design patterns](design-patterns.md) table picks the Route row.
- In the framing STOP, also ask for the options cheapest first, whether over-routing to a costlier
  option that also succeeds is accepted, and the success test for each option's output.
- For items declined or left unanswered, assume these, record them as assumed, and continue:
  - Fallback below the Choice threshold: the most capable option.
  - Options: those in the repository's configuration, else no route question is written.
  - Over-routing: not accepted.
- When no record shows which option succeeds on a case, run each option once on the frozen input,
  apply the success test, and label from the results with provenance `outcome`.
- The correct option is the cheapest that succeeds, or with over-routing accepted, any that
  succeeds and costs no less; `scores.csv` labels a case 1 when Jev's top option is correct.
