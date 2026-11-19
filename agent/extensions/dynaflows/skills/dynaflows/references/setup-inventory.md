# Harness setup: Step 1 inventory deltas

## Objective

Collect the inputs the record needs and the rule-ID evidence that decides each check step's owner.

## Guidance

- Discover make or task-runner targets, pre-commit or other hook manager config, CI workflow job names and the default
  branch's required checks from `gh api repos/<owner>/<name>/branches/<default>/protection` and
  `gh api repos/<owner>/<name>/rules/branches/<default>`. A failed query is recorded as unknown, never as
  "no required checks".
- When the record exists, read its `profile.md` and record its tiers, entry points, ownership areas, affected-T2
  selection, verification surface and per-machine budget approvals.
- For each bespoke checker, name the `guardrails check` step whose rule family it enforces (`STEPS` in
  `guardrails/whole_repo.py`), the rule IDs it enforces with the source line for each, the `checks.commit` chain that
  runs it (for example make target to script) and how it lists files.
- A step's rule IDs are the catalogue rows (`guardrails/data/guardrail-rules.json`) whose `reference.subcommand`
  names it. Search the repo's checkers for every rule ID of every step and record a source line or `absent` for each.
- A checker that walks the disk instead of `git ls-files --cached --others --exclude-standard`, or follows symlinks
  out of the repo, is a gap to fix in the repo checker.
- Record any baseline each repo checker or tool applies, with its finding count; a baselined finding is an open finding.
- Map the dependency-audit scope (`SECURITY.dependency-audit`) against the catalogue row: an audit that prints findings
  in another format than the row's `advisory_line` is a gap. Record the repo's dependency-audit exception file, if any,
  for the record's `ownerOnlyPaths`.
