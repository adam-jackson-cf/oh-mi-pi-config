# Candidate implementation

## Objective

Prepare complete, runnable target-stack controls and repo fixes outside the target checkout.

## Required actions

1. Work in a temporary clone of the target under the candidate root. Reuse target tools only where their pinned version and
   configuration express the catalogue semantics; otherwise prepare a pinned tool or parser-based checker in the candidate tree.
   Never overwrite established values or invent approvals.
2. Propose native tool configs: run `python3 <skill>/scripts/propose_tool_configs.py --repo <temporary clone> --out <candidate root>/proposed`
   (expect exit 0; exit 2 means `--out` lies inside the clone). It renders the tool rows of [the catalogue](../assets/guardrail-rules.json),
   writes a config only where the tool has none and reports kept configs and values it cannot derive.
   Merge a proposal into an existing config only for missing catalogue settings; a differing existing value is a Step 5 decision.
   Apply [tooling families](tooling-families.md).
3. For Python, adapt the [python reference](../assets/python-reference/README.md) checkers (`PYQ`, `PYS`, `FANOUT`):
   replace source-specific roots and allowlists from the target inventory and approved ownership maps; preserve exact nesting,
   import-count and threshold semantics. Empty allowlists grant nothing. A target without Python maps the portable semantics to its language.
4. In `grow` and `adopt` work, run each candidate checker and the repo's existing checker on the same clone. Sort every finding
   one reports and the other passes:
   - the repo checker lacks the rule, scopes it narrower, baselines it or misses files: add the fix to that family's `grow` change;
   - the repo checker's own exception or setting accepts the code: the repo checker is the authority, so drop the candidate setting;
   - never change repo code only to satisfy a duplicate copy.
   Zero findings on both sides shows no gap but not coverage; coverage comes from Step 2's rule-ID evidence.
   Draft each `grow` change in the repo's own style, with tests for every added rule.
5. Draft `proofs.toml` in the candidate tree from [the template](../assets/templates/proofs.toml), with the Step 0 scope.
   Write one claim per control (a tool, a checker, the test runner), naming its catalogue IDs or family patterns, its control paths,
   every deterministic point that runs it (`enforced_by`: `file` and the `ref` text that invokes the control; CI points name their
   `status`) and its canaries (`[[claim.canary]]`) run through the enforcement entrypoint. Write the `[validator]` job, the `[hooks]` installation check,
   approved `[[not_applicable]]` exclusions with evidence and approval, `[[exception]]` entries for repo checkers, and only the
   settings sections with no native home (`[structure]`, `[layout]`, `[test_budget]`, `[python_quality]`, `[surface]`,
   `[identity_artefacts]`). Fill only values with evidence. Pending exclusions and approvals stay in `state.json` `decisions` until
   Step 5, so the structure-only run lists those rules as missing. Values a native config holds stay there.
6. Run structure, naming and test-placement checks (`STRUCT.dependency-direction`, `STRUCT.folder-placement`, `NAME.conventions`,
   `TEST.placement`) over added and renamed paths from the `proofs.toml` maps and patterns, inside the same enforcement points as
   every other check. They exit 0 when clean, otherwise nonzero with one error per line naming the file, rule ID and concrete fix.
7. Prepare test-evidence checks per [test evidence](test-evidence.md): normalised run reports and collect-only test IDs per tier,
   never console text; a budget checker with per-machine limits; diff-aware growth and duplicate checks; isolation, mocking,
   evasion and seed source patterns. Author an adapter when the test framework has none. Keep reports outside the worktree:
   checkers and the processes they start write nothing into the repo tree, including bytecode and caches.
8. Draft the enforcement wiring per [gate wiring](gate-wiring.md): a check-only runner with tier selectors, one hook entry through
   the repo's hook manager (or a native `.githooks/pre-commit`), CI that runs the same entrypoint on every push and pull request
   as one job or parallel tier jobs with an always-running aggregate, and the validator as its own CI job posting the
   `[validator]` status. Draft host settings for `GIT.required-checks` requiring every claim's CI status and the validator status,
   with a live readback plan, and `CODEOWNERS` entries for `proofs.toml`, the validator, CI workflow files and owner-only paths.
9. For every applicable rule, prepare compliant and violating fixtures: the intended checker accepts the positive and rejects the
   negative, naming the rule ID. Cover valid/forbidden edges, correct/wrong destinations and names, below/over budget reports,
   named/generic test names, distinct/duplicate fingerprints, matched/divergent entrypoints, working/bypassed hook failures
   and protected/unprotected host settings. A tool claim needs one canary per tool through its enforcement entrypoint;
   a repo checker needs a canary per bespoke rule it enforces, naming it in `rule`. A rule that needs a base ref or repo-wide
   state gets a canary command that builds that scenario in a temporary repository from the fixture and runs the checker
   through the entrypoint there. Keep every fixture
   under one canary fixtures directory at the destination the repository's own placement checks accept (the one their
   diagnostic names), recorded where the owning checker reads its layout (else `proofs.toml` `[layout]`) and excluded from
   every checker's routine scan as an evidenced fixture path; prove each violating canary still fails when passed
   explicitly (a tool's forced exclusion must not pass it silently). Rules whose limits need Step 5 approval (budgets) stay
   blocked until then. Never exercise a canary by weakening real protection.
10. Plan to ship `proofs.toml` and `validate_rule_coverage.py` (standard-library Python 3.11+) into the repo, never the catalogue.
    Render the candidate files into the temporary clone, then run `python3 <skill>/scripts/validate_rule_coverage.py --root
    <temporary clone> --proofs <candidate root>/proofs.toml --catalog <skill>/assets/guardrail-rules.json --structure-only`
    against the draft. Never invent CI execution, fixture invocation or host settings.
11. Prepare fixes for every finding the candidate checks report in the clone inside the chosen scope, without weakening protection;
    Step 4 measures them, and each Step 4 rerun after a fix replaces the earlier views. Exclude only evidenced generated,
    vendored or fixture paths. Draft contributor commands separately from application-user guidance.
    Identify superseded checks and config to remove, including files that exist only for agent tooling or this setup.
    Never rewrite ADRs or frozen, digest-pinned study or evaluation records.

## Done when

- Every applicable ID has a runnable candidate and positive/negative proof inputs, or an explicit blocker.
- All candidates, proposals and the draft `proofs.toml` stay under the candidate root; the only target changes are `.proofs/` and
  its `.git/info/exclude` entry.
