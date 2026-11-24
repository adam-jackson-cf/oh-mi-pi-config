---
{
  "tiers": {
    "t1": {
      "command": "bash scripts/run-test-tier.sh t1"
    },
    "t2": {
      "command": "bash scripts/run-test-tier.sh t2"
    },
    "t3": {
      "command": "make test-slow"
    }
  },
  "adapters": [
    {
      "name": "bun_test",
      "tiers": {
        "t1": {
          "selector": "--test-name-pattern=.",
          "location": [
            "tests/extension.test.ts"
          ]
        },
        "t2": {
          "selector": "--test-name-pattern=.",
          "location": [
            "tests/extension-seams.test.ts"
          ]
        }
      }
    },
    {
      "name": "pytest",
      "tiers": {
        "t1": {
          "selector": "fast",
          "location": "tests"
        },
        "t2": {
          "selector": "seam",
          "location": "tests"
        },
        "t3": {
          "selector": "slow",
          "location": "tests"
        }
      }
    }
  ],
  "public_entry_points": {
    "ledger": "ledger.cli:main"
  },
  "run_events": {
    "commit": "bash scripts/run-ci-quality-gates.sh --tier commit",
    "ci": "bash scripts/run-ci-quality-gates.sh",
    "nightly_scheduler": ".github/workflows/nightly.yml at 03:17 UTC",
    "nightly_branches": "main"
  },
  "fast_tier_budget": {
    "duration_report_command": "bash scripts/run-test-tier.sh t1 with junit reports, then guardrails budget-check",
    "machines": [
      {
        "id": "m-541ca9da8504",
        "platform": "Darwin arm64",
        "runtime": "go 1.26.10",
        "wall_limit_s": 159,
        "per_test_limit_s": 46,
        "approval": "2026-11-05; limit increases require owner approval"
      }
    ]
  },
  "coverage": {
    "scope": [
      "src/ledger"
    ],
    "line_floor": 85,
    "branch_floor": 75,
    "where": "pyproject.toml"
  },
  "guards": {
    "process": "tests/unit/conftest.py",
    "network": "tests/unit/conftest.py",
    "user_data": "guardrails guard-run"
  },
  "environments": {
    "required": [
      "postgres"
    ],
    "require_flag": "--require-env",
    "readiness_probe": "make doctor"
  },
  "verification_surface": {
    "lever_command": "go run ./cmd/lib",
    "doctor_marker": "lib: startup failed",
    "feature_map_path": "docs/features",
    "journey_file_pattern": "*_scenario_test.go",
    "data_dir_arg": "--root",
    "evidence_dir_arg": "--json"
  },
  "identity_artefacts": {
    "agent/extensions/rails/judgments/**": "calibrated judgment data; change only through a recalibration"
  },
  "layout": {
    "support_dir": "tests/support",
    "fixtures_dir": "tests/fixtures",
    "test_file_patterns": [
      "test_*.py"
    ]
  },
  "command_notes": {
    "working_directory": "repository root after uv sync",
    "interpretation": "exit 0 passes; exit 1 lists findings"
  },
  "ownership_areas": {
    "map": {
      "tests/test_workflows_seams.py": "agent-rails"
    },
    "affected_t2_selection": "scripts/run-ci-quality-gates.sh --tier commit selects each framework T2 by staged paths"
  },
  "structure": {
    "owners": {
      "files": {
        "scripts/ci_gate.py": "ci-runner",
        "readme.md": "repository-documentation"
      },
      "patterns": {
        "agent/extensions/rails/**": "agent-rails",
        "tests/fixtures/**": "repository-proofs"
      }
    },
    "tiers": [],
    "import_direction": {
      "package": "guardrails",
      "source_root": "agent/extensions/rails/engine/guardrails"
    }
  },
  "house_style": {
    "version": 1
  },
  "guardrails": {
    "paths": {
      "skip": [
        "third_party/**"
      ]
    }
  }
}
---

# Plain lib profile
