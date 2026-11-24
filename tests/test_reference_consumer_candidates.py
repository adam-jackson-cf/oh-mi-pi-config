"""Committed trees for the structure-naming consumer-file proposer and its consumer-candidates command."""

from __future__ import annotations

import json
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

from guardrails.commands.structure import consumer_candidates

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/dynaflows/engine"
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/reference/structure/consumer-candidates.json").read_text()
)


@pytest.mark.fast
class ConsumerCandidateTests(unittest.TestCase):
    def test_each_tree_proposes_exactly_its_candidate_files(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                found = consumer_candidates.consumer_candidates(
                    FIXTURE["tree"], set(case["changed"]), case.get("profile", {})
                )
                assert [item["path"] for item in found] == case["candidates"]

    def test_a_candidate_carries_the_file_and_package_evidence(self) -> None:
        (found,) = consumer_candidates.consumer_candidates(
            FIXTURE["tree"], {"pkg/dashboard.py"}, {}
        )
        assert found["run_markers"] == ["streamlit"]
        assert found["det_score"] == 0.85
        assert found["state"]["package"] == {
            "name": "pkg",
            "description": "Runs pricing experiments.",
        }
        assert found["state"]["file"]["imports"] == [
            "import streamlit",
            "from pkg import core",
        ]


@pytest.mark.seam
class ConsumerCandidatesCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-consumer-candidates-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        tree = FIXTURE["tree"]
        self.base = self.commit({
            path: text for path, text in tree.items() if path != "pkg/dashboard.py"
        })
        self.head = self.commit({"pkg/dashboard.py": tree["pkg/dashboard.py"]})

    def git(self, *args: str) -> str:
        return run_text(
            ["git", "-C", str(self.repo), *args], check=True, timeout=10
        ).stdout.strip()

    def commit(self, files: dict[str, str]) -> str:
        for path, text in files.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_the_command_prints_the_changed_candidate_files(self) -> None:
        result = run_text(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "from guardrails.commands.cli import main; raise SystemExit(main())",
                str(REFERENCE),
                "consumer-candidates",
                "--repo",
                str(self.repo),
                "--base",
                self.base,
                "--head",
                self.head,
            ],
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        found: list[dict[str, Any]] = json.loads(result.stdout)["candidates"]
        assert [item["path"] for item in found] == ["pkg/dashboard.py"]

    def test_unreadable_commits_are_a_finding(self) -> None:
        (finding,) = consumer_candidates.run(
            self.repo, {}, ["--base", self.base, "--head", "missing"]
        )
        assert (finding.rule_id, finding.path) == ("STRUCTURE.consumer-candidates", ".")


if __name__ == "__main__":
    unittest.main()
