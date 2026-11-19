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
    "version": "<installed house-style version>"
  },
  "guardrails": {
    "exceptions": [{"rule": "<rule id>", "path": "<path or glob>", "reason": "<owner-approved reason>"}]
  }
}
---

# Harness record profile

The front matter above is the record's profile template: `guardrails init` drafts a live
`profile.md` from it, and its JSON is the authoritative value set. Every shared section, from
`tiers` to `command_notes`, has the meaning, fill rules and allowed exemptions of the generic
[test profile template](../../extensions/dynaflows/skills/test-guidance/assets/templates/test-profile.md).
This template adds only the harness fields below.

- Repo file paths are repo-relative; record file paths are record-relative.
- `fast_tier_budget.machines[].id` is the machine id the budget checker's probe reports.
- `adapter` names the test-framework adapter that produces the profile's test evidence.
- `structure` holds capability owners, tiers (lowest first; empty for a flat package) and
  import direction.
- `house_style.version` records the installed house-style version.
- `guardrails` holds per-checker settings and owner-approved `exceptions`, as
  [the record contract](README.md#profile-schema-and-checker-ownership) defines.
