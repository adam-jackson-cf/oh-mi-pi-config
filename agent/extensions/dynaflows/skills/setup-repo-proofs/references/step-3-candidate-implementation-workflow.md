# Candidate implementation

## Objective

Prepare candidate controls and repository fixes outside the target checkout.

## Required actions

1. Work in a temporary clone under the candidate root. Reuse project tools and settings; propose
   native-tool configuration separately from live configuration. Never overwrite established values
   or invent approvals. An unsupported rule stays blocked until its implementation and tests exist.
2. Draft missing-rule changes in the existing checker's style, with tests for every added rule.
   Grade every candidate test with the scenario rubric (`skill://test-guidance/references/case-selection.md#scenario-rubric`) before drafting it;
   draft keep and extend-existing verdicts only and report each drop with its failing criterion.
   Resolve discrepancies using the owning control's source evidence, not by changing product code
   to satisfy a duplicate checker. Clean output alone does not establish rule coverage.
3. Make test evidence machine-readable: normalized run reports and collect-only test IDs per tier,
   not console text. Keep temporary reports outside the worktree. Validate missing, invalid and
   stale reports; diagnostic failures identify the test and concrete corrective action.
4. Draft a check-only full runner with tier selectors and push/PR CI wiring. CI runs the full fast
   and boundary suites as one required job or parallel jobs of the same runner whose union equals
   the full suite. An aggregate required status depends on every tier, runs even after dependency
   failure, and succeeds only if every tier succeeds; failed, cancelled or skipped tiers fail it.
5. Preserve the project's commit and CI commands. The commit gate includes static checks, every
   fast-tier test and affected boundary tests. A project may invoke it through its own hook manager.
   Do not add a pre-push hook; propose removal of an existing one for approval.
6. Prepare fixes for every dry-run finding without weakening protection. Exclude only evidenced
   generated, vendored or fixture paths. Keep contributor commands distinct from application-user
   guidance. Identify superseded checks/configuration for removal. Preserve historical ADRs and
   digest-pinned study/evaluation artifacts unchanged.

## Done when

Every applicable ID has a candidate implementation or explicit blocker. All candidates remain
outside the target; no target file has been written, staged or replaced.
