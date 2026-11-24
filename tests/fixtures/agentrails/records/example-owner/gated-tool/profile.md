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
      "none": "no T3 suites: the repository has no exploratory or scheduled tests",
      "approval": "owner chat 2026-11-19: approved T3 exemption (D1)"
    }
  },
  "adapters": [
    {
      "name": "bun_test",
      "tiers": {
        "t1": {
          "selector": "--test-name-pattern=.",
          "location": [
            "tests/dynaflows-extension.test.ts",
            "tests/repo-record.test.ts"
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
        }
      }
    }
  ],
  "public_entry_points": {
    "guardrails": "guardrails.commands.cli:main",
    "repo_record": "repo_record:resolve_record",
    "agent/extensions/dynaflows/lib/repo-record.ts": [
      "resolveRecord"
    ]
  },
  "run_events": {
    "commit": "bash scripts/run-ci-quality-gates.sh --tier commit",
    "ci": "bash scripts/run-ci-quality-gates.sh",
    "nightly_scheduler": {
      "none": "no nightly scheduler: CI runs on the events the record chooses",
      "approval": "owner chat 2026-11-19: all approved"
    },
    "nightly_branches": "No nightly run: the nightly scheduler is exempt"
  },
  "fast_tier_budget": {
    "duration_report_command": "bun test ./tests/dynaflows-extension.test.ts ./tests/repo-record.test.ts",
    "machines": [
      {
        "id": "m-a1565ce7fd61",
        "platform": "Darwin arm64",
        "runtime": "bun 1.4.4",
        "wall_limit_s": 2,
        "per_test_limit_s": 1,
        "approval": "owner chat 2026-11-19: approved fast-tier budget 2.0 s (D5)"
      },
      {
        "id": "m-b411bed4a2ea",
        "platform": "Darwin arm64",
        "runtime": "python 3.14.10",
        "wall_limit_s": 2,
        "per_test_limit_s": 1,
        "approval": "owner chat 2026-11-19: approved fast-tier budget 2.0 s (D5)"
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
    "none": "no runnable app: the repository holds agent configuration",
    "approval": "owner chat 2026-11-19: all approved"
  },
  "identity_artefacts": {
    "agent/extensions/dynaflows/judgments/**": "calibrated judgment data; change only through a recalibration"
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
      "tests/dynaflows-extension-seams.test.ts": "dynaflows",
      "tests/repo-record-seams.test.ts": "repository-records"
    },
    "affected_t2_selection": "scripts/run-ci-quality-gates.sh --tier commit selects each framework T2 by staged paths"
  },
  "structure": {
    "owners": {
      "files": {
        "scripts/ci_gate.py": "ci-runner"
      },
      "patterns": {
        "agent/extensions/dynaflows/**": "dynaflows",
        "agent/extensions/dynaflows/skills/structured-workflow/**": "dynaflows",
        "agent/skills/jev-actions/**": "jev-actions"
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
      "source_root": "agent/extensions/dynaflows/engine/guardrails"
    }
  },
  "house_style": {
    "version": 1
  },
  "guardrails": {
    "check": {
      "run": [
        "profile-check",
        "python-quality",
        "python-fanout",
        "python-scalability",
        "surface-lint",
        "adapter lint",
        "python-test-hygiene"
      ],
      "exclude": [
        "tests/fixtures/**",
        "agent/extensions/dynaflows/engine/fixtures/**"
      ]
    },
    "coverage_ratchet": {
      "floor_keys": [
        "fail_under",
        "line_floor"
      ],
      "approvals": [
        {
          "path": "pyproject.toml",
          "approval": "owner chat 2026-11-20: fixture-tree coverage exclusions approved"
        }
      ]
    },
    "python": {
      "maintained_roots": [
        "agent/extensions/dynaflows/engine",
        "agent/skills/jev-actions/scripts",
        "agent/extensions/dynaflows/lib"
      ],
      "source_roots": [
        "agent/extensions/dynaflows/engine",
        "scripts"
      ]
    },
    "paths": {
      "skip": [
        "tests/fixtures/**"
      ]
    },
    "guarded_run": {
      "watch": [
        ".omp",
        ".ssh",
        ".config"
      ]
    }
  }
}
---

# Gated tool profile

Synthetic record prose.
