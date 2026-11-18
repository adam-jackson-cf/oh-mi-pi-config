# Test tiers and run events

## Objective

Assign each test one tier and report green only for the event actually exercised.

## Start here

- These rules are guidance. The repo test profile supplies values and may tighten them.
- Where this skill is silent, use judgement: keep existing content, make no irreversible write,
  ask the user once when the choice changes the outcome, and state the choice in the result.
- The calling workflow supplies the repo test profile's location.
  If it is unknown, ask once; never guess a profile from a repo-local skill.
  Read its values using the shipped [profile template](../assets/templates/test-profile.md) field names.
  Require every profile value; absent, empty, placeholder, n/a, TBD or unapproved none is missing.
  Only Verification surface, Nightly scheduler and Identity-bearing artefacts allow exemptions,
  each with an owner-approved reason and approval reference.
- Start with tiers, case selection, naming/layout and outcome tests; add property tests for pure cores.
  Read the surface runbook for mapped features, then before-commit and suite-budget before delivery.
  Read determinism, statistics, flakes and suite-review when their named topic applies.
- Report missing profile or surface values to the calling workflow and follow repo delivery policy.
  Continue only work not dependent on the missing values.
- With a valid surface exemption, use outcome tests; do not invent an application surface.
- During setup, check that any profile intended for version control is not ignored, using `git check-ignore`.
  Re-include its exact path when necessary; never force-add it.

## Membership

- T1 local fast: outcome tests through the public entry point, using only in-process storage (unique temp directory
  or SQLite file), and property tests on pure cores.
  A T1 test, including its setup and teardown, never starts a subprocess or opens a network connection;
  a test that needs either belongs in T2.
- T2 boundary: real process, build, transport, server-database or loopback seams, and scripted feature-map journeys.
- T3 scheduled: agent exploration over the feature map, live paid or LLM calls, soak, sweeps, security/performance
  phase-exit checks, formal checks, mutation testing and feature-map drift. Never move tests here to evade a budget.
- A public entry point is the interface a caller outside the owning package or feature uses: CLI entry, API handler
  or exported package API. Read each package's entry point from Tiers. Reach internals through it,
  except property tests on pure cores.
  If the changed area's public entry point is missing, ask once for the owner-approved entry point;
  do not write tests against a guessed private function.
- Map tiers onto existing repo tiers, never create parallel ones. Tier by what a test drives, not duration.
  Tier selectors are disjoint and complete: each test file belongs to exactly one tier and every test file
  is selected. Check with the runner's collect-only listing per tier.
- Probe readiness at the first selected test, never collection. Use Environments' require flag so missing environments fail.
- Run boundary suites through their entry point. Select affected T2 using Ownership areas' Affected T2 test selection.
  Hand an unmapped T2 file's missing mapping to the workflow that owns setup before claiming green.
  Unknown changed-production-code ownership is different: run all T2 and report that selection.

## Run events

- Commit: static checks, all T1, affected T2 boundary tests (seams and journeys).
- CI: all T1 and T2 before merge. Local runs never replace this gate.
- Nightly: T1, T2 and T3 on scheduler-enumerated active branches, as Run events records.
  If Nightly scheduler is exempt, report no nightly run; do not imply a scheduler exists.
- Without a CI command report `no CI event`, not CI green; also report the missing profile value.
  Name the event and commands exercised, never an unqualified all-tier pass.

## Assertion ownership

- Require a journey only when the user outcome crosses a process, build or UI boundary.
  Otherwise point the feature map to the T1 outcome test, including library repos.
- T1 owns exact values reachable through its public entry point or a pure-core property.
  A journey owns the mapped user end state across the boundary, not a duplicate parser/computation assertion.
  It may own an exact end-state value only when no T1 path reaches that value.
  For a pure-core bug, the property asserts computed values; the journey asserts their user-visible consequence.
  Seams assert only what crosses the boundary, never error prose.
- Give each assertion one owner. On a move, move fixtures and remove the superseded copy.
