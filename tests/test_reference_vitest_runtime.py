"""Vitest's external runtime boundaries; Node-only tests need no Vitest installation.

Real framework conformance is explicitly invoked with the target's pinned local Vitest;
its command fails closed when that installation is absent, rather than skipping cases.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands.adapters.vitest import conformance
from guardrails.commands.adapters.vitest.dialect import (
    DIALECT,
    guard_evidence,
    junit_arguments,
)
from guardrails.toolkit.test_report.junit import parse_text

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

REFERENCE = ROOT / "agent/extensions/dynaflows/engine"

RUNTIME = REFERENCE / "guardrails/commands/adapters/vitest/runtime.ts"


class VitestRuntimeCase(unittest.TestCase):
    def node(self, source: str, tier: str = "t1") -> dict[str, Any]:
        node = shutil.which("node")
        assert node is not None, (
            "activate the repository's pinned Node before running runtime tests"
        )
        with tempfile.TemporaryDirectory(prefix="vitest-native-guards-") as scratch:
            root = Path(scratch).resolve()
            home, allowed = root / "real-home", root / "allowed"
            home.mkdir()
            allowed.mkdir()
            events = root / "events.jsonl"
            events.write_text("", encoding="utf-8")
            script = (
                "import fs from 'node:fs';\n"
                "import fsp from 'node:fs/promises';\n"
                "import path from 'node:path';\n"
                "import { spawnSync } from 'node:child_process';\n"
                "import { connect } from 'node:net';\n"
                "import { createSocket } from 'node:dgram';\n"
                f"import {{ install }} from {json.dumps(RUNTIME.as_uri())};\n"
                f"const home = {json.dumps(str(home))};\n"
                f"const allowed = {json.dumps(str(allowed))};\n"
                f"const events = {json.dumps(str(events))};\n"
                f"const violations = install({{tier:{json.dumps(tier)}, protectedHome:home, allowed:[allowed], events}});\n"
                + source
                + "\nconsole.log(JSON.stringify({violations, events:fs.readFileSync(events,'utf8').trim().split('\\n').map(JSON.parse)}));\n"
            )
            result = run_text(
                [node, "--input-type=module", "-e", script], timeout=30, check=False
            )
            assert result.returncode == 0, result.stderr
            outcome: dict[str, Any] = json.loads(result.stdout)
            return outcome


class VitestRuntimeTests(VitestRuntimeCase):
    def test_junit_arguments_keep_configuration_native_and_evidence_external(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            report = Path(scratch) / "run.xml"
            arguments = junit_arguments("t1", str(report))
            assert arguments[0] == "--reporter"
            assert "--config" not in arguments
            assert "--no-cache" in arguments
            assert "--no-fsModuleCache" in arguments
            entry = Path(arguments[1])
            assert entry.parent == report.parent.resolve()
            assert json.dumps(str(report.resolve())) in entry.read_text()
            assert {item.name for item in report.parent.iterdir()} == {entry.name}
        with pytest.raises(ValueError, match="unknown Vitest tier"):
            junit_arguments("unknown", "unused.xml")

    def test_dialect_preserves_native_names_durations_skips_expected_failures_and_retries(
        self,
    ) -> None:
        report = parse_text(
            """<testsuite><testcase file="tests/case.test.ts" classname="tests/case.test.ts"
 name="suite &gt; case" time="0.125"><properties><property name="retries" value="2"/></properties>
 <skipped type="xfail" message="known defect"/></testcase>
 <testcase file="tests/case.test.ts" name="skipped"><skipped message="waiting on vendor fix"/></testcase></testsuite>""",
            DIALECT,
            "synthetic",
        )
        expected, skipped = report.cases
        assert (
            expected.id,
            expected.duration_s,
            expected.outcome,
            expected.reason,
            expected.retries,
        ) == ("tests/case.test.ts::suite > case", 0.125, "xfail", "known defect", 2)
        assert (skipped.outcome, skipped.reason) == ("skipped", "waiting on vendor fix")

    def test_guard_evidence_rejects_missing_incomplete_and_wrong_tier_reports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            report = Path(scratch) / "run.xml"
            assert guard_evidence(report, "t1")[0].rule_id == "TEST.failure-propagation"
            report.write_text(
                '<testsuite><properties><property name="omp.tier" value="t1"/>'
                '<property name="omp.guards" value="user-data"/></properties></testsuite>'
            )
            assert [finding.rule_id for finding in guard_evidence(report, "t1")] == [
                "TEST.fast-tier-isolation"
            ]
            assert guard_evidence(report, "t2")[0].rule_id == "TEST.failure-propagation"

    def test_every_canonical_case_has_a_frozen_typescript_fixture(self) -> None:
        cases = conformance.load_cases()
        assert len({case["id"] for case in cases}) == len(cases)
        for case in cases:
            with self.subTest(case=case["id"]):
                assert conformance.fixture_source(case["id"]).strip()
        assert conformance.fixture_source("owned-package").strip()

    def test_conformance_never_silently_skips_a_missing_native_installation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            results = conformance.run_conformance(repo=Path(scratch))
        assert [result.case_id for result in results] == [
            case["id"] for case in conformance.load_cases()
        ]
        assert all(
            not result.passed and "installed locally" in result.detail
            for result in results
        )
        with pytest.raises(ValueError, match="unknown conformance"):
            conformance.run_conformance(["not-a-case"])


if __name__ == "__main__":
    unittest.main()
