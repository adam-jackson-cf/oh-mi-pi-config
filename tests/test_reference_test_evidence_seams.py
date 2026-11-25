from __future__ import annotations

import contextlib
import io
import re
import sys
import unittest
from pathlib import Path
from typing import Any

from guardrails.host.process import run_attached, run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)


import pytest
from test_reference_test_evidence import (
    REVISION,
    RepoTestCase,
    profile,
)

from guardrails.commands.test_evidence import budgets
from guardrails.toolkit.test_report import collection

pytestmark = pytest.mark.seam


def bun_profile() -> dict[str, Any]:
    base = profile()
    base["tests"]["layout"]["test_file_patterns"] = ["test_*.py", "*.test.ts"]
    base["tests"]["adapters"].append({
        "name": "bun_test",
        "tiers": {"t1": {"selector": "-t unit", "location": "tests/unit"}},
    })
    return base


class MachineProbeTests(unittest.TestCase):
    def test_the_local_probe_identifies_this_machine(self) -> None:
        machine = budgets.probe_machine("python 3.12.4")
        assert re.search(r"^m-[0-9a-f]{12}$", machine.id)
        assert machine.id == budgets.probe_machine("python 3.12.4").id


class BudgetCommandTests(RepoTestCase):
    def test_without_git_or_revision_the_report_cannot_be_fresh(self) -> None:
        report = self.copy_fixture("pytest-clean.xml")
        findings = budgets.run(self.repo, profile(), ["--junit", report])
        assert "cannot be determined" in findings[0].message

    def test_the_revision_defaults_to_the_repository_head(self) -> None:
        git = ["git", "-C", str(self.repo)]
        run_attached([*git, "init", "-q"], check=True, timeout=30)
        run_attached(
            [
                *git,
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.invalid",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "x",
            ],
            check=True,
            timeout=30,
        )
        head = run_text(
            [*git, "rev-parse", "HEAD"], check=True, timeout=30
        ).stdout.strip()
        report = self.copy_fixture("pytest-clean.xml", REVISION, head)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            assert budgets.run(self.repo, profile(), ["--junit", report]) == []
        assert "no approved budget" in err.getvalue()


class CollectionTests(RepoTestCase):
    def setUp(self) -> None:
        super().setUp()
        run_attached(
            ["git", "-C", str(self.repo), "init", "-q"], check=True, timeout=30
        )
        for path in (
            "tests/unit/test_core.py",
            "tests/flow/test_journey.py",
            "tests/explore/test_agent.py",
            "tests/support/helpers.py",
        ):
            self.write(path, "")

    def lists(self, **tiers: str) -> list[str]:
        argv = []
        for tier, ids in tiers.items():
            self.write(f"lists/{tier}.txt", ids)
            argv.extend(["--tier", f"{tier}=lists/{tier}.txt"])
        return argv

    def clean(self) -> dict[str, str]:
        return {
            "t1": "# collected\ntests/unit/test_core.py::test_a\ntests/unit/test_core.py::test_b\n",
            "t2": "tests/flow/test_journey.py::test_checkout\n",
            "t3": "tests/explore/test_agent.py::test_explore\n",
        }

    def test_disjoint_tiers_selecting_every_test_file_pass(self) -> None:
        assert collection.run(self.repo, profile(), self.lists(**self.clean())) == []

    def test_a_tier_joins_the_lists_of_every_adapter_in_it(self) -> None:
        self.write("tests/unit/core.test.ts", "")
        self.write("lists/t1-bun.txt", "tests/unit/core.test.ts::totals add up\n")
        argv = [*self.lists(**self.clean()), "--tier", "t1=lists/t1-bun.txt"]
        assert collection.run(self.repo, bun_profile(), argv) == []
        self.write("lists/t1-bun.txt", "")
        assert [f.message for f in collection.run(self.repo, bun_profile(), argv)] == [
            "test file is selected by no tier"
        ]

    def test_a_file_selected_by_two_tiers_is_refused(self) -> None:
        tiers = {
            **self.clean(),
            "t2": "tests/flow/test_journey.py::test_checkout\ntests/unit/test_core.py::test_c\n",
        }
        findings = collection.run(self.repo, profile(), self.lists(**tiers))
        assert [(f.rule_id, f.path, f.message) for f in findings] == [
            (
                "TEST.tier-selectors",
                "tests/unit/test_core.py",
                "selected by tiers t1, t2",
            )
        ]

    def test_a_test_id_collected_twice_is_refused(self) -> None:
        tiers = {
            **self.clean(),
            "t3": "tests/explore/test_agent.py::test_explore\n" * 2,
        }
        findings = collection.run(self.repo, profile(), self.lists(**tiers))
        assert [f.message for f in findings] == [
            "tests/explore/test_agent.py::test_explore is collected 2 times in tier t3"
        ]

    def test_an_unselected_test_file_is_refused_unless_exempt(self) -> None:
        self.write("tests/unit/test_orphan.py", "")
        findings = collection.run(self.repo, profile(), self.lists(**self.clean()))
        assert [(f.path, f.message) for f in findings] == [
            ("tests/unit/test_orphan.py", "test file is selected by no tier")
        ]
        exempt = profile(unselected_exempt=["tests/unit/test_orphan.py"])
        assert collection.run(self.repo, exempt, self.lists(**self.clean())) == []

    def test_empty_missing_and_absent_tier_lists_are_refused(self) -> None:
        tiers = {**self.clean(), "t2": "# nothing collected\n"}
        argv = self.lists(**tiers)
        assert ("TEST.failure-propagation", "tier t2 collected no tests") in {
            (f.rule_id, f.message) for f in collection.run(self.repo, profile(), argv)
        }
        argv = [
            *self.lists(t1=self.clean()["t1"], t2=self.clean()["t2"]),
            "--tier",
            "t3=lists/absent.txt",
        ]
        assert [f.message for f in collection.run(self.repo, profile(), argv)] == [
            "tier t3 collect-only list is missing"
        ]
        argv = self.lists(t1=self.clean()["t1"], t2=self.clean()["t2"])
        assert "tier t3 has no collect-only list" in [
            f.message for f in collection.run(self.repo, profile(), argv)
        ]

    def test_unknown_tiers_and_bad_arguments_are_refused(self) -> None:
        argv = [*self.lists(**self.clean()), "--tier", "t9=lists/t1.txt"]
        assert [f.message for f in collection.run(self.repo, profile(), argv)] == [
            "tier t9 is not a profile tier"
        ]
        for bad in (["--tier", "t1"], ["--tier"], ["--tier", "t1=a", "--tier", "t1=a"]):
            with self.subTest(argv=bad):
                assert "usage" in collection.run(self.repo, profile(), bad)[0].message

    def test_collection_check_runs_through_the_cli(self) -> None:
        self.write_profile(profile())
        code, out, _ = self.cli("collection-check", *self.lists(**self.clean()))
        assert (code, out.strip()) == (0, "collection-check: ok")
        self.write("tests/unit/test_orphan.py", "")
        code, out, _ = self.cli("collection-check", *self.lists(**self.clean()))
        assert code == 1
        assert "tests/unit/test_orphan.py: TEST.tier-selectors" in out


class BudgetCliTests(RepoTestCase):
    def test_report_check_never_reads_test_timing(self) -> None:
        self.write_profile(
            profile(dialect={"source_suffix": ".py", "xfail_markers": ["pytest.xfail"]})
        )
        report = self.copy_fixture("pytest-clean.xml", 'value="1.400"', 'value="9.000"')
        code, out, err = self.cli(
            "report-check", "--junit", report, "--revision", "stale"
        )
        assert (code, out.strip(), err) == (0, "report-check: ok", "")

    def test_each_check_blocks_a_missing_report_with_its_own_rule(self) -> None:
        self.write_profile(profile())
        for subcommand, rule in (
            ("report-check", "TEST.failure-propagation"),
            ("budget-check", "TEST.budget.fast-tier-time"),
        ):
            with self.subTest(subcommand=subcommand):
                code, out, _ = self.cli(subcommand, "--junit", "reports/absent.xml")
                assert code == 1
                assert rule in out
                assert (
                    "TEST.failure-propagation"
                    if rule != "TEST.failure-propagation"
                    else "TEST.budget"
                ) not in out

    def test_budgets_skip_reports_of_slower_tiers(self) -> None:
        report = self.copy_fixture("pytest-clean.xml", 'value="t1"', 'value="t2"')
        argv = ["--junit", report, "--revision", "stale"]
        assert budgets.run(self.repo, profile(), argv) == []


if __name__ == "__main__":
    unittest.main()
