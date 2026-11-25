from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import approval, fixture

from guardrails.commands.adapters import bun_test
from guardrails.commands.adapters.bun_test import tiers
from guardrails.commands.adapters.bun_test.dialect import DIALECT
from guardrails.context.config.profile import GUARDRAILS_SECTIONS
from guardrails.toolkit.test_report.junit import ReportError, parse_report

pytestmark = pytest.mark.fast

PROFILE: dict[str, Any] = {
    "tests": {
        "adapters": [
            {
                "name": "bun_test",
                "tiers": {
                    "t1": {"selector": "--test-name-pattern fast", "location": "tests"},
                    "t2": {"selector": "--test-name-pattern slow", "location": "tests"},
                },
            }
        ],
        "tiers": {"t3": {"none": True}},
    },
    "approvals": [
        *fixture()["approvals"],
        approval("exemption", "tiers.t3", "no exploratory Bun tier"),
    ],
}


class BunAdapterWiringTests(unittest.TestCase):
    def test_registered_profile_section_and_all_actions(self) -> None:
        assert "adapter_bun_test" in GUARDRAILS_SECTIONS
        parser = bun_test.parser()
        choices = next(
            action.choices for action in parser._actions if action.dest == "action"
        )
        assert choices is not None
        assert set(choices) == {
            "lint",
            "tiers",
            "junit-args",
            "guard-evidence",
            "affected",
            "mutation-tool",
            "mutation-report",
            "conformance",
        }

    def test_mutation_tool_names_strykers_command_runner(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert bun_test.run(Path.cwd(), {}, ["mutation-tool"]) == []
        assert "@stryker-mutator/core@9.4.0" in out.getvalue()
        assert "commandRunner 'bun test'" in out.getvalue()

    def test_evidence_inside_the_repository_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            found = bun_test.run(
                repo, PROFILE, ["junit-args", "--tier", "t1", "--out", "report.xml"]
            )
            assert [item.rule_id for item in found] == [
                "GUARDRAILS.output-outside-repo"
            ]

    def test_missing_runner_and_selector_are_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            assert tiers.collect_tier(repo, {}, "t1", repo.parent / "evidence")
            with pytest.raises(ValueError, match="nonempty argv list"):
                tiers.command({"checkers": {"adapter_bun_test": {"command": "bun"}}})

    def test_a_report_without_its_journal_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            report = Path(scratch, "report.xml")
            report.write_text('<testsuites tests="0"></testsuites>', encoding="utf-8")
            with pytest.raises(ReportError, match="journal"):
                parse_report(report, DIALECT)


if __name__ == "__main__":
    unittest.main()
