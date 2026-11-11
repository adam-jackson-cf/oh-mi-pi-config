---
{
  "tiers": {
    "t1": {"selector": "not boundary", "location": "tests/unit", "command": "make test-fast"},
    "t2": {"selector": "boundary", "location": "tests/boundary", "command": "make test-boundary"},
    "t3": {"selector": "scheduled", "location": "tests/scheduled", "command": "make test-nightly"}
  },
  "public_entry_points": {"ledger": "ledger.cli:main"},
  "run_events": {
    "commit": "make gate-commit",
    "ci": "make gate-ci",
    "nightly_scheduler": {"none": "no scheduler host yet", "approval": "ADR-0007"},
    "nightly_branches": ["main"]
  },
  "fast_tier_budget": {
    "duration_report_command": "make test-fast-report",
    "machines": [
      {
        "id": "a1b2c3d4e5f60718",
        "platform": "darwin-arm64",
        "runtime": "python 3.12",
        "wall_limit_s": 20,
        "per_test_limit_s": 0.5,
        "approval": "ADR-0008"
      }
    ]
  },
  "coverage": {"scope": ["src/ledger"], "line_floor": 85, "branch_floor": 75, "where": "pyproject.toml"},
  "guards": {
    "process": "tests/unit/conftest.py",
    "network": "tests/unit/conftest.py",
    "user_data": "omp-guardrails guard-run"
  },
  "environments": {"required": ["postgres"], "require_flag": "--require-env", "readiness_probe": "make doctor"},
  "verification_surface": {"none": "library without a runnable app", "approval": "ADR-0009"},
  "ownership_areas": {
    "map": {"tests/boundary/test_ledger_*.py": "ledger"},
    "affected_t2_selection": "omp-guardrails adapter affected"
  },
  "identity_artefacts": {"none": "no identity-bearing files", "approval": "ADR-0009"},
  "layout": {
    "support_dir": "tests/support",
    "fixtures_dir": "tests/fixtures",
    "test_file_patterns": ["test_*.py"]
  },
  "command_notes": {
    "working_directory": "repository root after uv sync",
    "interpretation": "exit 0 passes; exit 1 lists findings"
  },
  "adapter": "pytest",
  "structure": {
    "owners": {"directories": {"src/ledger/": "ledger"}},
    "tiers": [],
    "import_direction": {"package": "ledger", "source_root": "src/ledger"}
  },
  "house_style": {"version": 1, "exceptions": []},
  "guardrails": {"naming": {"version_exempt": ["docs/"]}}
}
---

# Repo test profile

A complete profile used by the reference package's own tests.
