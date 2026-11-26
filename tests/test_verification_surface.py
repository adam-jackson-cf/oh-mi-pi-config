"""SURFACE.feature-map, SURFACE.journeys and SURFACE.lever-isolation of the guardrails engine."""

from __future__ import annotations

import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.surface import lever_selftest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

ENGINE = ROOT / "agent/extensions/agent-rails/engine"

FAKE_LEVER = ROOT / "tests/fixtures/surface/fake-lever"

LEVER = "scripts/lever"

DOCTOR_MARKER = "LEVER-DOCTOR-FAILED"


def surface_repo_config(**overrides: str) -> dict[str, Any]:
    values = {
        "lever_command": LEVER,
        "doctor_marker": DOCTOR_MARKER,
        "feature_map_path": "docs/features",
        "journey_file_pattern": "tests/journeys/test_*_journey.py",
        "data_dir_arg": "--data-dir",
        "evidence_dir_arg": "--evidence-dir",
    }
    values.update(overrides)
    return {
        "tests": {
            "verification_surface": values,
            "layout": {"test_file_patterns": ["test_*.py"]},
        }
    }


class LeverSelftestCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-lever-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)

    def selftest(self, mode: str, **settings: object) -> list[Any]:
        lever = shlex.join([sys.executable, str(FAKE_LEVER), "--mode", mode])
        repo_config = surface_repo_config(lever_command=lever)
        repo_config["checkers"] = {
            "lever_selftest": {"doctor_args": ["doctor"], **settings}
        }
        return lever_selftest.run(self.repo, repo_config, [])

    def messages(self, findings: list[Any]) -> str:
        assert all(f.rule_id == "SURFACE.lever-isolation" for f in findings)
        return " | ".join(finding.message for finding in findings)


class LeverSelftestTests(LeverSelftestCase):
    def test_incomplete_surface_is_reported(self) -> None:
        repo_config = surface_repo_config(doctor_marker="")
        findings = lever_selftest.run(self.repo, repo_config, [])
        assert "needs lever_command, doctor_marker" in self.messages(findings)


if __name__ == "__main__":
    unittest.main()
