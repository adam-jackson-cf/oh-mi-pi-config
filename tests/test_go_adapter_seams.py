from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import approval, config, fixture, write_config
from test_diff_checks import FixtureRepo

from guardrails.commands import cli
from guardrails.commands.adapters.conformance import load_cases
from guardrails.commands.adapters.go import lint as go_lint
from guardrails.commands.adapters.go.conformance import run_conformance
from guardrails.host.process import run_text
from guardrails.test_report.collect_list import read_ids

pytestmark = pytest.mark.seam

REPO_CONFIG: dict[str, Any] = {
    "tests": {
        "adapters": [
            {
                "name": "go",
                "tiers": {
                    "t1": {"selector": "-run ^TestBasket", "location": "shop"},
                    "t2": {"selector": "-run ^TestReport", "location": "shop"},
                },
            }
        ],
        "public_entry_points": {"shop": "example.com/shop/shop:Total"},
        "tiers": {
            "t1": {"command": "guardrails adapter go run --tier t1"},
            "t3": {"none": True},
        },
        "layout": {"test_file_patterns": ["*_test.go"]},
    },
    "approvals": [
        *fixture()["approvals"],
        approval("exemption", "tiers.t3", "no exploratory Go tier"),
    ],
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
    initialised = run_text(
        ["git", "init", "-q", str(repo)],
        cwd=root,
        env=os.environ,
        timeout=60,
        check=False,
    )
    assert initialised.returncode == 0, initialised.stderr
    write_config(repo, config(**REPO_CONFIG))
    return repo, root


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
            located = ["--repo", str(repo)]
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


AFFECTED_REPO_CONFIG = {
    **REPO_CONFIG,
    "tests": {
        **REPO_CONFIG.get("tests", {}),
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
    },
    "structure": {
        "owners": {"files": {}, "directories": {"shop/": "shop"}},
        "ownership_areas": {"map": {"shop/total_test.go": "shop"}},
    },
}


class GoAffectedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(MODULE, AFFECTED_REPO_CONFIG)
        self.located = ["--repo", str(self.repo.path)]

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


class GoSourceSetTests(unittest.TestCase):
    def test_ignored_files_are_no_use_and_unparseable_files_are_findings(self) -> None:
        files = {
            "go.mod": "module example.com/shop\n\ngo 1.22\n",
            "shop/core.go": "package shop\n\nfunc Helper() int { return 1 }\n",
            "shop/gen.go": (
                '//go:build ignore\n\npackage main\n\nimport "example.com/shop/shop"\n\n'
                "func main() { shop.Helper() }\n"
            ),
            "shop/testdata/use.go": (
                'package use\n\nimport "example.com/shop/shop"\n\n'
                "func Use() int { return shop.Helper() }\n"
            ),
            "shop/broken.go": "package shop\n\nfunc Broken( {\n",
            "tests/core_test.go": (
                'package tests\n\nimport (\n\t"testing"\n\n\t"example.com/shop/shop"\n)\n\n'
                "func TestHelperReturnsOne(t *testing.T) {\n"
                "\tif shop.Helper() != 1 {\n\t\tt.Fatal()\n\t}\n}\n"
            ),
        }
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            for path, text in files.items():
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text(text)
            run_text(["git", "init", "-q", str(repo)], check=True, timeout=60)
            run_text(["git", "-C", str(repo), "add", "."], check=True, timeout=60)
            config = {
                "tests": {
                    "public_entry_points": {"shop": "example.com/shop/shop:Total"}
                }
            }
            found = go_lint.lint(repo, config, [])
        assert sorted((item.path, item.message.split(":")[0]) for item in found) == [
            ("shop/broken.go", "cannot parse source for AST analysis"),
            ("shop/core.go", "line 3"),
        ]
        assert {item.rule_id for item in found} == {"TEST.no-test-hooks"}


if __name__ == "__main__":
    unittest.main()
