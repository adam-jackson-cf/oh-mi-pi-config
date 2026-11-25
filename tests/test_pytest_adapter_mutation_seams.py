from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import pytest
from test_pytest_adapter import (
    FIXTURES,
    PYTEST_ONLY,
    run_cli,
    write_repo_config,
)
from test_pytest_adapter_seams import GIT_ENV

from guardrails.commands.adapters.pytest import mutation
from guardrails.host.process import run_bytes

pytestmark = pytest.mark.seam


class MutationTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        self.out = Path(scratch.name).resolve() / "out"
        self.repo.mkdir()
        self.out.mkdir()
        self.report = str(self.out / "m.json")
        write_repo_config(self.repo, PYTEST_ONLY)
        source = FIXTURES / "mutmut-run"
        self.core = self.repo / "src/ledger/core.py"
        self.core.parent.mkdir(parents=True)
        text = (source / "core.py.txt").read_text()
        self.core.write_text(text.replace("value > high", "high < value"))
        self.commit()
        self.core.write_text(text)
        self.commit()
        meta = self.repo / "mutants/src/ledger/core.py.meta"
        meta.parent.mkdir(parents=True)
        meta.write_bytes((source / "core.py.meta").read_bytes())

    def commit(self) -> None:
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(GIT_ENV)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            run_bytes(
                ["git", "-C", str(self.repo), *args],
                check=True,
                env={**environment, "GIT_CONFIG_GLOBAL": os.devnull},
                timeout=60,
            )

    def test_mutmut_results_normalise_to_function_spans(self) -> None:
        code, out = run_cli(
            "adapter",
            "--repo",
            str(self.repo),
            "pytest",
            "mutation-report",
            "--out",
            self.report,
        )
        assert code == 0, out
        report = json.loads(Path(self.report).read_text())
        spans = {
            (m["id"].rsplit(".", 1)[1], m["start_line"], m["end_line"], m["status"])
            for m in report["mutants"]
        }
        assert spans == {
            ("x_total__mutmut_1", 1, 2, "killed"),
            ("x_clamp__mutmut_1", 5, 10, "survived"),
            ("x_clamp__mutmut_2", 5, 10, "survived"),
            ("xǁAccountǁdeposit__mutmut_1", 14, 15, "killed"),
        }
        assert {m["path"] for m in report["mutants"]} == {"src/ledger/core.py"}

    def test_surviving_mutant_in_changed_code_fails_the_check(self) -> None:
        run_cli(
            "adapter",
            "--repo",
            str(self.repo),
            "pytest",
            "mutation-report",
            "--out",
            self.report,
        )
        code, out = run_cli(
            "mutation-check",
            "--repo",
            str(self.repo),
            "--report",
            self.report,
            "--base",
            "HEAD~1",
        )
        assert code == 1
        findings, guidance = out.split("remediation TEST.mutation-sensitivity: ")
        assert findings.count("TEST.mutation-sensitivity") == 2
        assert guidance.startswith("root cause: ")
        assert "x_clamp__mutmut_1 survived in changed code" in out
        assert "x_total" not in out

    def test_killed_mutants_of_changed_code_pass_the_check(self) -> None:
        meta = self.repo / "mutants/src/ledger/core.py.meta"
        data = json.loads(meta.read_text())
        data["exit_code_by_key"] = dict.fromkeys(data["exit_code_by_key"], 1)
        meta.write_text(json.dumps(data))
        run_cli(
            "adapter",
            "--repo",
            str(self.repo),
            "pytest",
            "mutation-report",
            "--out",
            self.report,
        )
        code, out = run_cli(
            "mutation-check",
            "--repo",
            str(self.repo),
            "--report",
            self.report,
            "--base",
            "HEAD~1",
        )
        assert (code, out.strip()) == (0, "mutation-check: ok")

    def test_missing_mutmut_results_are_a_finding(self) -> None:
        (self.repo / "mutants/src/ledger/core.py.meta").unlink()
        code, out = run_cli(
            "adapter",
            "--repo",
            str(self.repo),
            "pytest",
            "mutation-report",
            "--out",
            self.report,
        )
        assert code == 1
        assert "no mutmut results" in out

    def test_exit_codes_map_to_shared_statuses(self) -> None:
        assert [mutation.status(code) for code in (1, 0, 33, 36, None, 2, 34)] == [
            "killed",
            "survived",
            "no_tests",
            "timeout",
            "incomplete",
            "incomplete",
            "incomplete",
        ]


if __name__ == "__main__":
    unittest.main()
