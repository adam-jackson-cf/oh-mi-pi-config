# Merge and replacement approval

## Objective

Make the user's scope and ownership decisions the boundary before target writes.

## Required actions

1. Present an action matrix: ID, rule IDs, current owner, proposed owner, audience, operation
   (`keep`, `merge`, `replace`, `add`, or `defer`), paths, dry-run change count, baseline
   violations, verification, and risk.
2. Explain the consequence of every replacement or exception, including whether a current hook,
   CI workflow, config, or developer document would be removed or changed.
3. For existing repositories, request an explicit operation for each conflicting practice. For a
   greenfield repository, propose a minimal central runner and only the stack-relevant rules; do
   not invent a CI provider.
4. Show the complete applicable rule-coverage result from `scripts/validate_rule_coverage.py`. A
   rule still blocked by missing implementation or proof stays blocked; do not offer a passing
   approval path by relabeling it.
5. Offer two tiers. `Minimal`: least change that gives `.omp/gate.json` (schema version 2) a
   `checks.commit` and a `pathCheck`, plus an installed pre-commit hook running `checks.commit`;
   state which applicable rules it leaves unenforced (narrower scope, not a relabel). `Full`:
   every applicable rule and recommendation, including `checks.ci` wired into CI by the gate's
   `ci.runner` role.
   Write the repo-relative candidate paths per tier to `.omp/guardrail-proposal.json` as
   `{"schemaVersion":1,"minimal":[...],"full":[...]}`; this file is the only target write
   allowed before approval.
6. Ask the user to choose a tier and approve exact candidate paths and merge/replace decisions,
   using `ask` with a question containing "guardrail" and options "Minimal" and "Full" (with
   dynaflows on, agent-rails blocks `.omp/gate.json` and proposal paths until this is
   answered). **STOP** until the user replies; elapsed time or a default option is not approval.
7. If the user chooses a narrower scope, recompute the candidate diff, coverage ledger and
   proposal file while preserving the complete applicable rule set inside that scope.

## Done when

- Approved scope, paths, conflict decisions, and rule deviations are explicit.
- No target mutation other than `.omp/guardrail-proposal.json` occurs before approval, and no
  blocker is represented as green.
