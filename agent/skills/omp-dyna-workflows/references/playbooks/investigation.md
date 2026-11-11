# Investigation playbook

Deliver traceable scratch-only evidence, not repository implementation.

Follow [the six-stage contract](../../SKILL.md#six-stage-contract) in order;
only the deltas below belong here. Ordinary implementation cannot skip its
owner-approved project-setup prerequisite. Helper mechanics live in
[patterns](../patterns.md); Git mechanics live in `skill://git-tool`.

## 1. Frame

State competing explanations, evidence predicates and questions that change the recommendation.

## 2. Spike

Deliver the saved spike report with answers, commands, actual outputs, failures,
uncertainty and recommendation. When `find` or `lsp` is unavailable, record the
gap and fall back to `ast_grep`, then `grep`/`glob`; do not repair source state
incidentally. The report is the deliverable; stop here.

## 3. Plan

Not executed. A separately requested implementation starts Frame in its own
playbook, using the report as evidence.

## 4. Implement

Not executed for evidence-only work.

## 5. Verify

Not executed for evidence-only work.

## 6. Integrate

Not executed for evidence-only work.
