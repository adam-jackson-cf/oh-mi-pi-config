# Investigation playbook

## Objective

Deliver traceable scratch-only evidence, not repository implementation.

## Stage deltas

- Evidence-only investigation needs no implementation record.
- Frame: state competing explanations, evidence predicates and the questions that change the recommendation.
- Spike: deliver the saved spike report with answers, commands, actual outputs, failures, uncertainty and recommendation.
- Spike: when `find` or `lsp` is unavailable, record the gap and fall back to `ast_grep`, then `grep`/`glob`;
  do not repair source state incidentally.
- The report is the deliverable: stop after Spike. Plan, Implement, Verify and Integrate are not executed.
- A separately requested implementation starts Frame in its own playbook, using the report as evidence.
