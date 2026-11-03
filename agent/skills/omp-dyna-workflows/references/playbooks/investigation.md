# Investigation playbook

## Objective

Answer the engineering question with traceable evidence without changing repository state.

## Steps

1. Define the question, competing explanations, and evidence that would distinguish them;
   keep the investigation read-only and make no code, configuration, test, or documentation edits.
2. Read `skill://codegraph/references/readiness-and-bootstrap.md`; assess graph readiness without
   installing, rebuilding, or otherwise writing repository state.
3. Read `skill://codegraph/references/query-selection.md`; use codegraph to trace relevant flows,
   ownership, and dependencies, then read only the source ranges needed to validate the result.
4. Read `skill://codegraph/references/index-maintenance.md` if the graph is stale; report required
   maintenance rather than performing a write during this investigation.
5. Read `skill://codegraph/references/mcp-registration.md` if registration is missing; report
   the prerequisite rather than changing configuration during this investigation.
6. Read `skill://codegraph/references/fallback-and-handoff.md` if graph access is unavailable
   or evidence must be handed off; use its read-only fallback and label remaining uncertainty.
7. Read `skill://jev-actions` if an eval is needed; apply the owner rules for deterministic
   checks, Jev judgments, and independent cross-family critique without repository writes.
8. Report the evidenced answer, affected paths, unresolved uncertainty, and recommended next
   action; route any requested implementation to a separate code-changing playbook.
