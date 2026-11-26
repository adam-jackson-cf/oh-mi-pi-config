from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)


import pytest
from test_agentrails_fixture import config, write_config
from test_bun_test_adapter import REPO_CONFIG

from guardrails.commands import cli
from guardrails.commands.adapters.bun_test.conformance import run_conformance
from guardrails.commands.adapters.bun_test.dialect import (
    DIALECT,
    guard_evidence,
    junit_arguments,
)
from guardrails.commands.adapters.conformance import load_cases
from guardrails.commands.test_evidence import propagation
from guardrails.test_report.collect_list import read_ids
from guardrails.test_report.junit import parse_report

pytestmark = pytest.mark.seam


BUN = shutil.which("bun")


SUITE = """\
import { describe, expect, test } from 'bun:test';
describe('cart', () => {
  describe('totals', () => {
    test('fast sum of two items', () => { expect(1 + 1).toBe(2); });
  });
});
test.each([1, 2])('fast row %i stays positive', value => { expect(value).toBeGreaterThan(0); });
let attempts = 0;
test('fast flaky total', () => { attempts += 1; expect(attempts).toBe(2); }, { retry: 2 });
test('slow product', () => { expect(2 * 2).toBe(4); });
test.failing('fast known defect', () => { expect(1).toBe(2); });
test.skip('fast waiting for vendor', () => {});
"""


def bun_repository(root: Path, files: dict[str, str]) -> Path:
    repo = root / "repo"
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    return repo


def run_tier(repo: Path, report: Path, tier: str, *selector: str) -> int:
    assert BUN is not None
    return run_text(
        [BUN, "test", *junit_arguments(tier, str(report)), *selector, "tests"],
        cwd=repo,
        timeout=300,
        check=False,
    ).returncode


class BunRunTests(unittest.TestCase):
    def test_every_conformance_case_passes(self) -> None:
        results = run_conformance()
        assert len(results) == len(load_cases())
        for result in results:
            with self.subTest(case=result.case_id):
                assert result.passed, result.detail

    def test_report_keeps_suite_ids_retries_expected_failures_and_drops_filtered(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = bun_repository(Path(scratch), {"tests/cart.test.ts": SUITE})
            report = Path(scratch, "evidence", "t1.xml")
            exit_code = run_tier(repo, report, "t1", "--test-name-pattern", "fast")
            parsed = parse_report(report, DIALECT)
            assert [
                (case.id, case.outcome, case.retries, case.reason)
                for case in parsed.cases
            ] == [
                (
                    "tests/cart.test.ts::cart > totals > fast sum of two items",
                    "passed",
                    0,
                    "",
                ),
                ("tests/cart.test.ts::fast row 1 stays positive", "passed", 0, ""),
                ("tests/cart.test.ts::fast row 2 stays positive", "passed", 0, ""),
                ("tests/cart.test.ts::fast flaky total", "passed", 1, ""),
                (
                    "tests/cart.test.ts::fast known defect",
                    "xfail",
                    0,
                    "fast known defect",
                ),
                (
                    "tests/cart.test.ts::fast waiting for vendor",
                    "skipped",
                    0,
                    "fast waiting for vendor",
                ),
            ]
            assert guard_evidence(report, "t1") == []
            messages = [
                finding.message
                for finding in propagation.evaluate(parsed, {}, exit_code)
            ]
            assert any("passed only after 1 retries" in item for item in messages)
            assert any("is xfail without approval" in item for item in messages)
            assert any("is skipped without approval" in item for item in messages)

    def test_report_needs_the_exit_code_because_load_errors_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = bun_repository(
                Path(scratch),
                {
                    "tests/cart.test.ts": (
                        "import { expect, test } from 'bun:test';\n"
                        "test('slow product', () => { expect(2 * 2).toBe(4); });\n"
                    ),
                    "tests/broken.test.ts": "throw new Error('cannot load');\n",
                },
            )
            report = Path(scratch, "evidence", "t2.xml")
            exit_code = run_tier(repo, report, "t2", "--test-name-pattern", "slow")
            parsed = parse_report(report, DIALECT)
            assert [(case.id, case.outcome) for case in parsed.cases] == [
                ("tests/cart.test.ts::slow product", "passed")
            ]
            assert exit_code != 0
            assert [
                finding.message for finding in propagation.evaluate(parsed, {}, None)
            ] == [
                "this runner's report omits failures outside the test cases, such as a "
                "test file that fails to load, and no exit code was given"
            ]
            assert [
                finding.message
                for finding in propagation.evaluate(parsed, {}, exit_code)
            ] == [f"runner exited {exit_code} although no reported case failed"]

    def test_cli_lists_each_tier_without_running_test_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            source = SUITE.replace(
                "describe('cart'",
                "import { writeFileSync } from 'node:fs';\n"
                "test('fast writes a marker', () => { writeFileSync('ran.txt', 'x'); });\n"
                "describe('cart'",
            )
            repo = bun_repository(Path(scratch), {"tests/cart.test.ts": source})
            run_text(["git", "init", "-q", str(repo)], check=True, timeout=60)
            write_config(repo, config(**REPO_CONFIG))
            out_dir = Path(scratch, "collections")
            location = ["--repo", str(repo)]
            with (
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                code = cli.main([
                    "adapter",
                    *location,
                    "bun_test",
                    "tiers",
                    "--out",
                    str(out_dir),
                ])
            assert code == 0
            assert not (repo / "ran.txt").exists()
            assert read_ids(out_dir / "t1.txt") == [
                "tests/cart.test.ts::cart > totals > fast sum of two items",
                "tests/cart.test.ts::fast flaky total",
                "tests/cart.test.ts::fast known defect",
                "tests/cart.test.ts::fast row 1 stays positive",
                "tests/cart.test.ts::fast row 2 stays positive",
                "tests/cart.test.ts::fast writes a marker",
            ]
            assert read_ids(out_dir / "t2.txt") == ["tests/cart.test.ts::slow product"]


if __name__ == "__main__":
    unittest.main()
