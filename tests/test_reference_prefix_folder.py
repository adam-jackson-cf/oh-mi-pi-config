"""Committed repositories for the reference STRUCT.prefix-folder check."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands.structure import prefix_folder
from guardrails.commands.structure.rename_residue import GitError
from guardrails.foundation.findings import catalogue_rules, remediations

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parent
        / "fixtures/reference/structure/prefix-folder.json"
    ).read_text()
)


@pytest.mark.fast
class PrefixFolderScoreTests(unittest.TestCase):
    def test_a_pair_score_weights_each_relationship_and_caps_co_change(self) -> None:
        weights = prefix_folder.contract()["weights"]
        features = {
            "import_edge": 1.0,
            "shared_reader": 0.0,
            "co_change": 0.5,
            "prefix_is_domain_word": 1.0,
        }
        assert prefix_folder.pair_score(features, weights) == 0.625
        assert prefix_folder.pair_score({**features, "co_change": 3.0}, weights) == 0.75

    def test_domain_words_come_from_docstrings_and_class_names(self) -> None:
        tree = prefix_folder.parsed(
            '"""Audit events."""\n\n\nclass InvoiceDrafts:\n    pass\n'
        )
        assert {"audit", "event", "invoice", "draft"} <= prefix_folder.domain_words(
            tree
        )

    def test_the_cutoff_weights_and_owner_evidence_live_in_the_catalogue(self) -> None:
        rule = next(
            item for item in catalogue_rules() if item["id"] == prefix_folder.RULE
        )
        contract = rule["contract"]
        assert contract["cluster_score_cutoff"] == 0.15
        assert set(contract["weights"]) == set(prefix_folder.FEATURES)
        assert contract["evidence"]["accepted_on"] == "2026-11-23"
        assert remediations()[prefix_folder.RULE].render().startswith("root cause: ")


@pytest.mark.fast
class ImportGraphTests(unittest.TestCase):
    def test_imports_resolve_to_the_nearest_owned_module(self) -> None:
        sources = {
            "pkg/__init__.py": "",
            "pkg/util.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/util.py": "",
            "pkg/sub/main.py": (
                "import util\nfrom ..... import nothing\n"
                "import pkg.util.missing.deeper\nimport nowhere.at.all\n"
                "from pkg import util as u\nfrom pkg.sub import *\n"
            ),
            "a/x/helpers.py": "",
            "a/y/helpers.py": "",
            "a/x/run.py": "import x.helpers\n",
            "c/helpers.py": "",
            "c/use.py": "import helpers\n",
            "tools/helpers.py": "",
            "b/main.py": "import helpers\n",
        }
        trees: dict[str, ast.Module | None] = {
            path: ast.parse(text) for path, text in sources.items()
        }
        trees["broken.py"] = None
        graph = prefix_folder.ImportGraph(trees)
        assert {
            path: sorted(found) for path, found in graph.edges.items() if found
        } == {
            "a/x/run.py": ["a/x/helpers.py"],
            "b/main.py": ["tools/helpers.py"],
            "c/use.py": ["c/helpers.py"],
            "pkg/sub/main.py": [
                "pkg/__init__.py",
                "pkg/sub/__init__.py",
                "pkg/sub/util.py",
                "pkg/util.py",
            ],
        }
        assert "broken.py" not in graph.edges
        assert prefix_folder.domain_words(None) == set()

    def test_no_blobs_need_no_git_and_a_failed_batch_is_a_git_error(self) -> None:
        assert prefix_folder.read_blobs(Path("."), []) == {}
        failed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"bad object")
        with (
            patch.object(prefix_folder, "run_bytes", return_value=failed),
            pytest.raises(GitError, match="bad object"),
        ):
            prefix_folder.read_blobs(Path("."), ["0" * 40])


@pytest.mark.seam
class PrefixFolderCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-prefix-folder-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def git(self, repo: Path, *args: str) -> str:
        return run_text(
            ["git", "-C", str(repo), *args], check=True, timeout=10
        ).stdout.strip()

    def commit(self, repo: Path, files: dict[str, str]) -> str:
        for path, text in files.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "fixture", "--allow-empty")
        return self.git(repo, "rev-parse", "HEAD")

    def repository(self, case: dict[str, Any]) -> tuple[Path, str, str]:
        repo = self.scratch / str(len(list(self.scratch.iterdir())))
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Fixture")
        self.git(repo, "config", "user.email", "fixture@example.invalid")
        self.commit(repo, FIXTURE["base"])
        base = self.commit(repo, case.get("base_extra", {}))
        return repo, base, self.commit(repo, case["head"])

    def test_each_repository_reports_exactly_its_prefix_clusters(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                repo, base, head = self.repository(case)
                found = prefix_folder.run(
                    repo, case.get("profile", {}), ["--base", base, "--head", head]
                )
                assert sorted((item.rule_id, item.path) for item in found) == sorted(
                    tuple(row) for row in case["findings"]
                )

    def test_the_finding_names_the_prefix_members_and_closest_pair(self) -> None:
        repo, base, head = self.repository(FIXTURE["cases"][0])
        (finding,) = prefix_folder.run(repo, {}, ["--base", base, "--head", head])
        assert finding.path == "src/shop/grading/"
        assert "`coverage`" in finding.message
        assert "src/shop/grading/coverage_offline.py" in finding.message
        assert "src/shop/grading/coverage_store.py" in finding.message
        assert "import_edge 1" in finding.message
        assert "`coverage/` folder" in finding.fix

    def test_a_logged_run_writes_a_reviewable_flag_row_per_cluster(self) -> None:
        import contextlib
        import io
        import os
        import sqlite3
        from unittest.mock import patch

        from guardrails.commands import cli

        repo, base, head = self.repository(FIXTURE["cases"][0])
        record = self.scratch / "records" / "owner" / "repo"
        record.mkdir(parents=True)
        (record / "profile.md").write_text("---\n{}\n---\n")
        agent = self.scratch / "agent"
        argv = ["prefix-folder", "--repo", str(repo), "--record", str(record)]
        argv += ["--log-decisions", "prove", "--base", base, "--head", head]
        with (
            patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(agent)}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            assert cli.main(argv) == 1
        with contextlib.closing(
            sqlite3.connect(agent / "dynaflows/dynaflows.db")
        ) as db:
            rows = db.execute(
                "SELECT source, judgment, question, outcome, item, trigger FROM decisions"
            ).fetchall()
        assert rows == [
            (
                "check",
                "STRUCT.prefix-folder",
                "prefix-folder",
                "flag",
                "src/shop/grading/",
                "prove",
            )
        ]

    def test_unreadable_commits_are_a_finding(self) -> None:
        repo, base, _ = self.repository(FIXTURE["cases"][0])
        (finding,) = prefix_folder.run(repo, {}, ["--base", base, "--head", "missing"])
        assert (finding.rule_id, finding.path) == (prefix_folder.RULE, ".")


if __name__ == "__main__":
    unittest.main()
