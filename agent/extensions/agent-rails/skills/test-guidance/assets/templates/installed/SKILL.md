---
name: "{{skill_name}}"
description: "Use when writing, changing, moving or deleting tests in {{repo_name}}."
---

# Task

## Precedence

- This repository's instruction files and the files each route names take precedence; this skill routes to them and fills gaps.
- Read the route for the task before starting it, and every file the route names, in order.

## Tiers and facts

{{tier_map}}
- Public entry points: {{entry_points}}.
- Layout: {{layout}}. Fixtures: {{fixtures}}. Support code: {{support}}.
- Production inputs: {{inputs_pattern}}.
- Name tests like `{{naming_example}}`.
- Coverage: {{coverage}}. Per-test limits: {{timeout_config}}.
- Boundary launch: {{boundary_launch}}.
- Property library: {{property_library}}.
- Golden files: {{golden_files}}.

## Every task

- Tier a test by its design (what it drives) first, then its cost; never move a test to another tier because of its speed alone.
- Never mock, stub, fake or runtime-patch this repository's own code. Put recorded or local substitutes behind the production adapter.
- Before handing over, run {{local_checks}}. {{merge_checks}}{{scheduled_checks}}Targeted runs never replace these.
- When a commit hook is installed, let it run and never bypass it. Automatic fixes: {{fix_command}}.
- A skipped, timed-out or zero-collected run is not a pass.
- Never add skip, xfail, only, retry or quarantine markers, rerun until green, raise a timeout or loosen an assertion
  to get a pass, unless the user asks for it.

## Task routes

- Writing, changing, moving or deleting a test: {{route_writing}}.
- Adding or changing a T2 boundary test or journey: {{route_boundary}}.
- A test passes and fails on the same tree: {{route_flaky}}.
- Reviewing or deleting tests, or changing the suite's run time: {{route_maintenance}}.
- Adding or changing a property test: {{route_property}}.
- Adding, changing or regenerating golden or snapshot files: {{route_golden}}.

## Repository decisions

{{repository_decisions}}
