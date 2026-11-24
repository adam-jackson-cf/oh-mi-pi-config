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
    "nightly_scheduler": {
      "none": "No scheduled test workflow; T1 and T2 run on every push.",
      "approval": "owner approval 2026-11-16"
    },
    "nightly_branches": "main and every pull request"
  },
  "fast_tier_budget": {
    "duration_report_command": "bash scripts/run-test-tier.sh t1 with junit reports, then guardrails budget-check",
    "machines": [
      {
        "id": "m-efd8d651c839",
        "platform": "Darwin arm64",
        "runtime": "python 3.12.16",
        "wall_limit_s": 40,
        "per_test_limit_s": 25,
        "approval": "owner approval 2026-11-16 from five guarded T1 runs"
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
    "lever_command": "python3 scripts/lever.py",
    "doctor_marker": "APP-DOCTOR-FAILED",
    "feature_map_path": "docs/features",
    "journey_file_pattern": "tests/journeys/*.journey.py",
    "data_dir_arg": "--data-dir",
    "evidence_dir_arg": "--evidence-dir"
  },
  "identity_artefacts": {
    "none": "The app keeps no identity-bearing artefacts: finding ids are content-derived per run.",
    "approval": "owner approval, guardrail setup Step 5, 2026-11-16"
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
    "tiers": [
      "host",
      "foundation",
      "context",
      "toolkit",
      "commands"
    ],
    "import_direction": {
      "package": "guardrails",
      "source_root": "agent/extensions/rails/engine/guardrails"
    }
  },
  "house_style": {
    "version": 1
  },
  "guardrails": {
    "exceptions": [
      {
        "rule": "NAME.conventions",
        "path": "docs/adr/**",
        "check": "child-repeats-parent",
        "reason": "ADR file names follow the ADR numbering convention"
      },
      {
        "rule": "NAME.conventions",
        "path": "docs/legacy/**",
        "reason": "frozen legacy docs (ADR-003)"
      }
    ],
    "check": {
      "run": [
        "profile-check",
        "surface-lint",
        "adapter lint"
      ],
      "exclude": [
        "tests/fixtures/**"
      ]
    },
    "coverage_ratchet": {
      "floor_keys": [
        "fail_under"
      ],
      "approvals": [
        {
          "path": "src/app/detector.py",
          "approval": "Owner approval 2026-11-21: pure rename; pragmas moved unchanged"
        }
      ]
    },
    "test_evidence": {
      "fast_tier": "t1",
      "unselected_exempt": [],
      "approved_skips": [
        {
          "id": "tests/test_slow.py::test_container_*",
          "reason": "needs a container runtime",
          "approval": "owner chat 2026-11-20"
        }
      ]
    },
    "naming": {
      "skip": [
        "tests/fixtures/**"
      ]
    }
  }
}
---

# Allowed app profile

Synthetic record prose: the cutover archives it and drops it from the config.
