"""The guardrails Bun adapter: conformance, dialect completion, collection and CLI wiring.

Runtime and collection cases run the real ``bun test`` this repository already uses.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)


import pytest

from guardrails import cli
from guardrails.adapters import bun_test
from guardrails.adapters.bun_test import tiers
from guardrails.adapters.bun_test.conformance import load_cases, run_conformance
from guardrails.adapters.bun_test.dialect import (
    DIALECT,
    guard_evidence,
    junit_arguments,
)
from guardrails.config.profile import GUARDRAILS_SECTIONS
from guardrails.test_evidence import propagation
from guardrails.test_evidence.collection import read_ids
from guardrails.test_evidence.junit import ReportError, parse_report

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
PROFILE: dict[str, Any] = {
    "adapters": [
        {
            "name": "bun_test",
            "tiers": {
                "t1": {"selector": "--test-name-pattern fast", "location": "tests"},
                "t2": {"selector": "--test-name-pattern slow", "location": "tests"},
            },
        }
    ],
}


def bun_repository(root: Path, files: dict[str, str]) -> Path:
    repo = root / "repo"
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    return repo


def run_tier(repo: Path, report: Path, tier: str, *selector: str) -> int:
    assert BUN is not None
    return subprocess.run(
        [BUN, "test", *junit_arguments(tier, str(report)), *selector, "tests"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    ).returncode


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
                tiers.command({"guardrails": {"adapter_bun_test": {"command": "bun"}}})

    def test_a_report_without_its_journal_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            report = Path(scratch, "report.xml")
            report.write_text('<testsuites tests="0"></testsuites>', encoding="utf-8")
            with pytest.raises(ReportError, match="journal"):
                parse_report(report, DIALECT)


@unittest.skipUnless(BUN, "bun is not on PATH")
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
            record = Path(scratch, "record")
            record.mkdir()
            (record / "profile.md").write_text(
                f"---\n{json.dumps(PROFILE)}\n---\n", encoding="utf-8"
            )
            out_dir = Path(scratch, "collections")
            location = ["--repo", str(repo), "--record", str(record)]
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
