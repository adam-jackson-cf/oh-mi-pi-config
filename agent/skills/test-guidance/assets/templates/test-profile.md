---
{
  "tiers": {
    "t1": {"selector": "<selector>", "location": "<location>", "command": "<command>"},
    "t2": {"selector": "<selector>", "location": "<location>", "command": "<command>"},
    "t3": {"selector": "<selector>", "location": "<location>", "command": "<command>"}
  },
  "public_entry_points": {"<package>": "<public entry point>"},
  "run_events": {
    "commit": "<command>",
    "ci": "<command>",
    "nightly_scheduler": "<scheduler, or the none-with-approval object>",
    "nightly_branches": "<branches or branch selection rule>"
  },
  "fast_tier_budget": {
    "duration_report_command": "<command>",
    "machines": [
      {
        "id": "<machine id from the budget checker's probe>",
        "platform": "<platform>",
        "runtime": "<runtime>",
        "wall_limit_s": "<wall limit in seconds>",
        "per_test_limit_s": "<per-test limit in seconds>",
        "approval": "<approval reference>"
      }
    ]
  },
  "coverage": {
    "scope": "<owned files and tiers>",
    "line_floor": "<line floor percent>",
    "branch_floor": "<branch floor percent>",
    "where": "<configuration or policy location>"
  },
  "guards": {
    "process": "<fast-tier process guard location>",
    "network": "<fast-tier network guard location>",
    "user_data": "<user-data guard and location>"
  },
  "environments": {
    "required": "<environments>",
    "require_flag": "<flag>",
    "readiness_probe": "<command or probe>"
  },
  "verification_surface": {
    "lever_command": "<command>",
    "doctor_marker": "<doctor failure marker>",
    "feature_map_path": "<path>",
    "journey_file_pattern": "<pattern>",
    "data_dir_arg": "<argument>",
    "evidence_dir_arg": "<argument>"
  },
  "ownership_areas": {
    "map": {"<T2 file pattern>": "<area>"},
    "affected_t2_selection": "<selection command or rule>"
  },
  "identity_artefacts": {"<path>": "<preservation rule>"},
  "layout": {
    "support_dir": "<directory>",
    "fixtures_dir": "<directory>",
    "test_file_patterns": ["<pattern>"]
  },
  "command_notes": {
    "working_directory": "<directory and prerequisites>",
    "interpretation": "<flags, exit codes, and interpretation>"
  },
  "adapter": "<test-framework adapter, e.g. pytest>",
  "structure": {
    "owners": {"files": {"<path>": "<owner>"}, "directories": {"<directory>/": "<owner>"}},
    "tiers": ["<lowest tier package>", "<higher tier package>"],
    "import_direction": {"package": "<import name>", "source_root": "<directory>"}
  },
  "house_style": {
    "version": "<installed house-style version>",
    "exceptions": [{"rule": "<rule id>", "path": "<path>", "adr": "<ADR reference>"}]
  },
  "guardrails": {}
}
---

# Repo test profile

Under DynaFlows this file is the record's `profile.md`: `repos/<owner>/<name>/profile.md` under the oh-mi-pi-config
root, gitignored and never committed. Repo paths in its settings are repo-relative; no setting names a harness
directory inside the repo.

Fill every section with values. `none: <owner-approved reason>` is allowed only in Verification
surface, Nightly scheduler (a Run events field), and Identity-bearing artefacts; elsewhere a
missing value is a reported gap.

The front matter above holds the same values as one JSON object; JSON is valid YAML, so the block
reads as ordinary front matter. Checkers, `freeze()` and `omp-guardrails profile-check`
(`PROFILE.complete`) read only that block, so keep it and the sections below in step.

- Replace every `<...>` placeholder with a value. Placeholders, `n/a`, `TBD`, empty values and a
  bare `none` count as missing.
- Write an allowed exemption as `{"none": "<reason>", "approval": "<reference>"}` in place of the
  `verification_surface` section, the `run_events.nightly_scheduler` field or the
  `identity_artefacts` section.
- Write limits and floors as numbers: `wall_limit_s` and `per_test_limit_s` in seconds,
  `line_floor` and `branch_floor` in percent.
- `structure.tiers` may be an empty list while the code stays flat in one package.
- `house_style.exceptions` may be an empty list; each entry needs an ADR reference.
- Approval = the owner edits the field by hand. Under DynaFlows this profile is the per-repo record,
  and rails refuse every agent write to it.
- `guardrails` holds per-checker settings such as `structure`, `naming` and `paths`; each
  checker's module documentation in the guardrails reference lists its settings.

## Tiers

- T1: selector or marker `<selector>`, location `<location>`, command `<command>`.
- T2: selector or marker `<selector>`, location `<location>`, command `<command>`.
- T3: selector or marker `<selector>`, location `<location>`, command `<command>`.
- Public entry point per package: `<package>`: `<public entry point>`.

## Run events

- Commit command: `<command>`.
- CI command: `<command>`.
- At the agent's commit, after the Commit command, the harness runs `omp-guardrails report-check`
  and `omp-guardrails budget-check` on the fast-tier report. CI runs the CI command only and never
  measures test timing.
- Nightly scheduler: `<scheduler or none: owner-approved reason>`.
- Nightly branches: `<branches or branch selection rule>`.

## Fast-tier budget

- Per machine: `<machine id>`, platform `<platform>`, runtime `<runtime>`.
- Per machine limits: wall `<wall limit>`, per-test `<per-test limit>`.
- Per machine owner approval reference: `<approval reference>`.
- Duration-report command: `<command>`.
- Budgets apply only at the agent's commit (`omp-guardrails budget-check`); CI never measures or
  reports test timing.

## Coverage

- Scope: `<owned files and tiers>`.
- Line floor: `<line floor>`; branch floor: `<branch floor>`.
- Where defined: `<configuration or policy location>`.
- Floors are met only by outcome or property cases or deleting unreachable code.

## Guards

- Fast-tier process guard location: `<location>`.
- Fast-tier network guard location: `<location>`.
- User-data guard: `<guard and location>`.

## Environments

- Required environments: `<environments>`.
- Require flag: `<flag>`.
- Readiness probe: `<command or probe>`.

## Verification surface

- Lever command: `<command>`.
- Doctor failure marker: `<marker>`.
- Feature map path: `<path>`.
- Journey file pattern: `<pattern>`.
- Data directory argument: `<argument>`.
- Evidence directory argument: `<argument>`.

## Ownership areas

- Map of T2 files to areas: `<file patterns and areas>`.
- Affected T2 test selection: `<selection command or rule>`.

## Identity-bearing artefacts

- Artefacts and preservation rules: `<paths and rules>`.

## Layout and naming

- Support directory: `<directory>`.
- Fixtures directory: `<directory>`.
- Test file patterns: `<patterns>`.

## Structure and house style

- Capability owners: `<files and directories with their owners>`.
- Tiers, lowest first, and import direction: `<tier packages and source root>`.
- House-style version and ADR-backed exceptions: `<version and exceptions>`.

## Command notes

- Working directory and prerequisites: `<directory and prerequisites>`.
- Flags, exit codes, and interpretation: `<command notes>`.
