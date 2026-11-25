"""Committed trees for the structure-naming consumer-file proposer and its consumer-candidates command."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import config, write_config

from guardrails.commands.structure_naming import consumer_candidates
from guardrails.context import changes

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "agent/extensions/agent-rails/engine"
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/structure/consumer-candidates.json").read_text()
)


@pytest.mark.fast
class ConsumerCandidateTests(unittest.TestCase):
    def test_each_tree_proposes_exactly_its_candidate_files(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                found = consumer_candidates.consumer_candidates(
                    FIXTURE["tree"], set(case["changed"]), case.get("config", {})
                )
                assert [item["path"] for item in found] == case["candidates"]

    def test_a_candidate_carries_the_file_and_package_evidence(self) -> None:
        (found,) = consumer_candidates.consumer_candidates(
            FIXTURE["tree"], {"pkg/dashboard.py"}, {}
        )
        assert found["run_markers"] == ["streamlit"]
        assert found["proposer_score"] == 0.85
        assert found["state"]["package"] == {
            "name": "pkg",
            "description": "Runs pricing experiments.",
        }
        assert found["state"]["file"]["imports"] == [
            "import streamlit",
            "from pkg import core",
        ]


@pytest.mark.fast
class ImportReadingTests(unittest.TestCase):
    def test_unparseable_source_falls_back_to_import_lines(self) -> None:
        source = "import a.b, c as d\nfrom ..x import (y, z as w)\ndef broken(:\n"
        assert consumer_candidates.parse_imports(source) == [
            ("a.b", [], 0, 1, "import a.b"),
            ("c", [], 0, 1, "import c"),
            ("x", ["y", "z"], 2, 2, "from ..x import (y, z as w)"),
        ]

    def test_only_literal_import_module_calls_count_as_imports(self) -> None:
        source = (
            "import importlib\nimportlib.import_module('.plugins')\n"
            "__import__(name)\nimport_module()\nf()()\nprint('x')\n"
        )
        assert consumer_candidates.parse_imports(source) == [
            ("importlib", [], 0, 1, "import importlib"),
            ("plugins", [], 0, 2, "import_module('.plugins')"),
        ]

    def test_relative_imports_climb_one_folder_per_extra_dot(self) -> None:
        assert consumer_candidates.resolve("a/b/c.py", "x", ["y", "*"], 2) == {
            "a.x",
            "a.x.y",
        }
        assert consumer_candidates.resolve("a/b/c.py", "", ["y"], 1) == {"a.b", "a.b.y"}

    def test_manifests_name_setup_pyproject_and_poetry_scripts(self) -> None:
        setup = "[options.entry_points]\nconsole_scripts =\n    tool = pkg.cli:main\n"
        pyproject = (
            "[project.scripts]\na = 'p.m:f'\n[project.gui-scripts]\n"
            "b = {reference = 'p.g:f'}\n[tool.poetry.scripts]\n"
            "c = {callable = 'p.c:f'}\nd = 3\n"
        )
        assert consumer_candidates.manifest_targets("setup.cfg", setup) == [
            ("tool", "pkg.cli:main")
        ]
        assert consumer_candidates.manifest_targets("pyproject.toml", pyproject) == [
            ("a", "p.m:f"),
            ("b", "p.g:f"),
            ("c", "p.c:f"),
        ]
        assert consumer_candidates.manifest_targets("pyproject.toml", "a = = b") == []

    def test_hidden_folders_and_skill_bundles_are_not_candidates(self) -> None:
        bundle = {"skills/SKILL.md": ""}
        assert consumer_candidates.excluded(".hidden/x.py", {})
        assert consumer_candidates.excluded("skills/s/x.py", bundle)
        assert not consumer_candidates.excluded("a/x.py", bundle)


@pytest.mark.seam
class ConsumerCandidatesCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-consumer-candidates-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        write_config(self.repo, config(**getattr(self, "profile", {})))
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
                str(ENGINE),
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

    def test_the_run_prints_candidates_from_the_head_tree(self) -> None:
        printed = io.StringIO()
        with redirect_stdout(printed):
            assert (
                consumer_candidates.run(
                    self.repo, {}, ["--base", self.base, "--head", self.head]
                )
                == []
            )
        found = json.loads(printed.getvalue())["candidates"]
        assert [item["path"] for item in found] == ["pkg/dashboard.py"]

    def test_the_head_tree_keeps_skill_manifests_and_drops_excluded_paths(self) -> None:
        tree = consumer_candidates.head_tree(
            self.repo, self.head, ["pkg/entrypoints/*"]
        )
        assert tree["pkg/skill/SKILL.md"] == ""
        assert tree["pkg/dashboard.py"] == FIXTURE["tree"]["pkg/dashboard.py"]
        assert not any(path.startswith("pkg/entrypoints/") for path in tree)
        assert consumer_candidates.head_tree(self.repo, self.head, ["*"]) == {}

    def test_an_unreadable_blob_batch_is_a_finding(self) -> None:
        failed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"bad object")
        with patch.object(changes, "run_bytes", return_value=failed):
            (finding,) = consumer_candidates.run(
                self.repo, {}, ["--base", self.base, "--head", self.head]
            )
        assert finding.message == "bad object"

    def test_unreadable_commits_are_a_finding(self) -> None:
        (finding,) = consumer_candidates.run(
            self.repo, {}, ["--base", self.base, "--head", "missing"]
        )
        assert (finding.rule_id, finding.path) == ("STRUCT.consumer-candidates", ".")


if __name__ == "__main__":
    unittest.main()
