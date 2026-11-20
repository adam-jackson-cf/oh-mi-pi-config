from __future__ import annotations

import json
import subprocess
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

pytestmark = pytest.mark.seam


ROOT = Path(__file__).resolve().parents[1]


REFERENCE = ROOT / "agent/extensions/dynaflows/engine"


class RenameResidueCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-rename-residue-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        self.record = Path(scratch.name) / "record"
        self.repo.mkdir()
        self.record.mkdir()
        self.profile: dict[str, Any] = {}
        self.write_profile()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")

    def git(self, *args: str) -> str:
        return run_text(
            ["git", "-C", str(self.repo), *args], check=True, timeout=10
        ).stdout.strip()

    def write_profile(self) -> None:
        (self.record / "profile.md").write_text(
            "---\n" + json.dumps(self.profile) + "\n---\n"
        )

    def write(self, path: str, text: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, subject: str) -> str:
        self.git("add", ".")
        self.git("commit", "-qm", subject, "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def move(self, old: str, new: str) -> None:
        target = self.repo / new
        target.parent.mkdir(parents=True, exist_ok=True)
        (self.repo / old).rename(target)

    def command(
        self, base: str, head: str = "HEAD"
    ) -> subprocess.CompletedProcess[str]:
        return run_text(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "from guardrails.commands.cli import main; raise SystemExit(main())",
                str(REFERENCE),
                "rename-residue",
                "--repo",
                str(self.repo),
                "--record",
                str(self.record),
                "--base",
                base,
                "--head",
                head,
            ],
            cwd=REFERENCE,
            check=False,
            timeout=20,
        )

    def assert_clean(self, result: subprocess.CompletedProcess[str]) -> None:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "RENAME.residue:" not in result.stdout

    def test_oracle_to_verifier_reports_exactly_four_findings(self) -> None:
        self.write(
            "oracle/grader.py", "def run_oracle():\n    return 'grader result'\n"
        )
        self.write(
            "oracle/runtime/raidar_oracle_plugin.py",
            "class OraclePlugin:\n"
            "    def _take_oracle_environment(self):\n"
            "        return 'plugin environment'\n",
        )
        base = self.commit("Oracle owner")
        self.move("oracle/grader.py", "verifier/grader.py")
        self.move(
            "oracle/runtime/raidar_oracle_plugin.py",
            "verifier/runtime/raidar_oracle_plugin.py",
        )
        self.commit("Rename oracle owner to verifier")
        result = self.command(base)
        assert result.returncode == 1, result.stdout + result.stderr
        lines = [
            line
            for line in result.stdout.splitlines()
            if "RENAME.residue:" in line and not line.startswith("remediation ")
        ]
        assert len(lines) == 4, result.stdout
        assert "remediation RENAME.residue: root cause: " in result.stdout
        for path, identifier in [
            ("verifier/grader.py", "run_oracle"),
            ("verifier/runtime/raidar_oracle_plugin.py", "raidar_oracle_plugin"),
            ("verifier/runtime/raidar_oracle_plugin.py", "OraclePlugin"),
            ("verifier/runtime/raidar_oracle_plugin.py", "_take_oracle_environment"),
        ]:
            assert any(
                line.startswith(path + ":") and f"defines `{identifier}`" in line
                for line in lines
            ), result.stdout

    def test_gate_effort_rename_keeps_gate_mechanism_valid(self) -> None:
        self.write(
            "scoring/gate_effort/scorer.py",
            '"""verification-effort: what the gate/1 checks cost the agent."""\n'
            'GATE_KEY = "gate/1"\n'
            "class GateRunView:\n    pass\n"
            "class StepGate:\n    pass\n",
        )
        base = self.commit("Gate effort metric")
        self.move(
            "scoring/gate_effort/scorer.py", "scoring/verification_effort/scorer.py"
        )
        self.commit("Rename metric while preserving gate mechanism")
        self.assert_clean(self.command(base))

    def test_prefix_homonym_outside_renamed_folder_is_valid(self) -> None:
        self.write(
            "grading/prefix/store.py", "def store():\n    return 'prefix record'\n"
        )
        self.write(
            "grading/sandbox.py", "def coverage_prefix():\n    return 'sandbox'\n"
        )
        base = self.commit("Prefix owner and unrelated homonym")
        self.move("grading/prefix/store.py", "grading/pre_fix/store.py")
        self.commit("Rename prefix owner only")
        self.assert_clean(self.command(base))

    def test_coverage_offline_regroup_is_valid(self) -> None:
        self.write(
            "grading/coverage_offline.py",
            "def coverage_offline():\n    return 'coverage'\n",
        )
        base = self.commit("Coverage offline module")
        self.move("grading/coverage_offline.py", "grading/coverage/offline.py")
        self.commit("Group coverage without retiring its words")
        self.assert_clean(self.command(base))

    def test_split_with_remaining_process_compliance_owner_is_valid(self) -> None:
        self.write(
            "process_compliance/checks.py",
            "def process_compliance_checks():\n    return 'checks'\n",
        )
        self.write("process_compliance/records.py", "CONTRACT = 'compliance records'\n")
        base = self.commit("Compliance owns checks and records")
        self.move("process_compliance/checks.py", "process_checks/checks.py")
        self.commit("Split checks from remaining compliance owner")
        self.assert_clean(self.command(base))

    def test_head_snapshot_ignores_dirty_worktree(self) -> None:
        self.write(
            "grading/oracle.py", "def run_oracle():\n    return 'renamed module'\n"
        )
        base = self.commit("Existing module")
        self.move("grading/oracle.py", "grading/verifier.py")
        head = self.commit("Rename one module")
        self.write("grading/verifier.py", "invalid uncommitted Python !!!")
        result = self.command(base, head)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "defines `run_oracle`" in result.stdout
        assert "cannot parse" not in result.stdout

    def test_documented_non_owned_paths_are_excluded(self) -> None:
        self.profile = {"guardrails": {"check": {"exclude": ["verifier/vendor"]}}}
        self.write_profile()
        self.write("oracle/api.py", "def run():\n    return 'owned API'\n")
        self.write("oracle/vendor/plugin.py", "class OraclePlugin:\n    pass\n")
        base = self.commit("Owned API and vendor plugin")
        self.move("oracle/api.py", "verifier/api.py")
        self.move("oracle/vendor/plugin.py", "verifier/vendor/plugin.py")
        self.commit("Move owner including unowned vendor")
        self.assert_clean(self.command(base))

    def test_pinned_rename_detection_overrides_disabled_repo_setting(self) -> None:
        self.write(
            "oracle/api.py", "def run_oracle():\n    return 'unique oracle source'\n"
        )
        base = self.commit("Existing owner")
        self.move("oracle/api.py", "verifier/api.py")
        self.commit("Rename owner")
        self.git("config", "diff.renames", "false")
        self.git("config", "diff.renameLimit", "1")
        result = self.command(base)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "defines `run_oracle`" in result.stdout


if __name__ == "__main__":
    unittest.main()
