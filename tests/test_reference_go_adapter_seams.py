from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest
from test_reference_diffs import FixtureRepo

from guardrails.commands import cli
from guardrails.commands.adapters.go.conformance import load_cases, run_conformance
from guardrails.commands.adapters.go.process import run_command
from guardrails.toolkit.test_report.collection import read_ids

pytestmark = pytest.mark.seam

PROFILE = {
    "adapters": [
        {
            "name": "go",
            "tiers": {"t1": {"selector": "-run ^TestBasket", "location": "shop"}},
        }
    ],
    "public_entry_points": {"shop": ["Total"]},
    "tiers": {"t1": {"command": "guardrails adapter go run --tier t1"}},
    "layout": {"test_file_patterns": ["*_test.go"]},
}

MODULE = {
    "go.mod": "module example.com/shop\n\ngo 1.22\n",
    "shop/total.go": "package shop\n\nfunc Total(prices []int) int {\n\tsum := 0\n\tfor _, price := range prices {\n\t\tsum += price\n\t}\n\treturn sum\n}\n",
    "shop/total_test.go": """package shop

import "testing"

func TestBasketTotalSumsPrices(t *testing.T) {
\tfor _, prices := range [][]int{{1, 2}, {3}} {
\t\tt.Run("rows", func(t *testing.T) {
\t\t\tif Total(prices) != 3 {
\t\t\t\tt.Fatal(prices)
\t\t\t}
\t\t})
\t}
}

func TestReportWritesNothing(t *testing.T) {}
""",
}


def run_cli(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(list(argv))
    return code, out.getvalue()


def shop_module(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    for name, text in MODULE.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    initialised = run_command(["git", "init", "-q", str(repo)], root, os.environ, 60)
    assert initialised.returncode == 0, initialised.stderr
    record = root / "record"
    record.mkdir()
    (record / "profile.md").write_text(f"---\n{json.dumps(PROFILE)}\n---\n")
    return repo, record


class GoAdapterTests(unittest.TestCase):
    def test_every_conformance_case_passes_with_the_go_toolchain(self) -> None:
        results = run_conformance()
        assert len(results) == len(load_cases())
        for result in results:
            with self.subTest(case=result.case_id):
                assert result.passed, result.detail

    def test_a_guarded_tier_run_and_listing_feed_the_shared_checks(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo, record = shop_module(Path(scratch))
            report = Path(scratch, "evidence", "t1.xml")
            located = ["--repo", str(repo), "--record", str(record)]
            code, out = run_cli(
                "adapter", *located, "go", "run", "--tier", "t1", "--out", str(report)
            )
            assert code == 0, out
            code, out = run_cli("report-check", *located, "--junit", str(report))
            assert (code, out.strip()) == (0, "report-check: ok")
            code, out = run_cli(
                "adapter", *located, "go", "guard-evidence", str(report), "--tier", "t1"
            )
            assert code == 0, out
            listing = Path(scratch, "lists")
            code, out = run_cli(
                "adapter", *located, "go", "tiers", "--out", str(listing)
            )
            assert code == 0, out
            assert read_ids(listing / "t1.txt") == [
                "shop/total_test.go::TestBasketTotalSumsPrices"
            ]


AFFECTED_PROFILE = {
    **PROFILE,
    "adapters": [
        {
            "name": "go",
            "tiers": {
                "t1": {"selector": "-run ^TestBasket", "location": "shop"},
                "t2": {"selector": "-run ^TestReport", "location": "shop"},
            },
        }
    ],
    "coverage": {"scope": ["shop"]},
    "ownership_areas": {"map": {"shop/total_test.go": "shop"}},
    "structure": {"owners": {"files": {}, "directories": {"shop/": "shop"}}},
}


class GoAffectedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(MODULE, AFFECTED_PROFILE)
        self.located = [
            "--repo",
            str(self.repo.path),
            "--record",
            str(self.repo.record),
        ]

    def test_a_changed_package_prints_its_t2_selector_and_package(self) -> None:
        self.repo.commit({"shop/total.go": MODULE["shop/total.go"] + "\n"})
        code, out = run_cli(
            "adapter", *self.located, "go", "affected", "--base", self.base
        )
        assert (code, out.splitlines()) == (0, ["-run '^TestReport' ./shop"])

    def test_an_unknown_base_is_a_finding(self) -> None:
        code, out = run_cli(
            "adapter", *self.located, "go", "affected", "--base", "no-such-ref"
        )
        assert code == 1
        assert "no-such-ref" in out


if __name__ == "__main__":
    unittest.main()
