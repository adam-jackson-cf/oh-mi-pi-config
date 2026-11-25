"""TEST.user-data-guard and TEST.fast-tier-isolation (network-none prefix) of guard-run."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.environment import guarded_run

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

REFERENCE = ROOT / "agent/extensions/agent-rails/engine"


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class GuardedRunCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-environment-")
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        self.real_home = root / "real-home"
        (self.real_home / ".config/app").mkdir(parents=True)
        (self.real_home / ".config/app/settings.json").write_text(
            "{}", encoding="utf-8"
        )
        (self.real_home / ".cache").mkdir()
        patcher = unittest.mock.patch.dict(os.environ, {"HOME": str(self.real_home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.profile = {"checkers": {"guarded_run": {"watch": [".config/app"]}}}

    def guard(
        self, argv: list[str], profile: dict[str, Any] | None = None
    ) -> list[Any]:
        return guarded_run.run(
            self.repo, self.profile if profile is None else profile, argv
        )


class GuardedRunTests(GuardedRunCase):
    def test_missing_watch_list_is_a_finding(self) -> None:
        findings = self.guard(["--", *python("pass")], {})
        assert [finding.rule_id for finding in findings] == ["TEST.user-data-guard"]
        assert "watch" in findings[0].message

    def test_network_none_without_prefix_is_a_fast_tier_isolation_finding(self) -> None:
        findings = self.guard(["--network-none", "--", *python("pass")])
        assert [finding.rule_id for finding in findings] == ["TEST.fast-tier-isolation"]


if __name__ == "__main__":
    unittest.main()
