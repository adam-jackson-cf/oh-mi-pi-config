"""Committed sibling fixtures for the reference PREFIX.candidates pre-check."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import config, write_config

from guardrails.commands.structure import prefix_candidates

pytestmark = pytest.mark.seam

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/agent-rails/engine"


class PrefixCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-prefix-candidates-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        self.repo.mkdir()
        self.profile: dict[str, Any] = {
            "structure": {
                "import_direction": {"source_root": "src/shop", "package": "shop"}
            }
        }
        self.git("init", "-q")
        write_config(self.repo, config(**getattr(self, "profile", {})))
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")

    def git(self, *args: str) -> str:
        return run_text(
            ["git", "-C", str(self.repo), *args], check=True, timeout=10
        ).stdout.strip()

    def write(self, path: str, text: str = "") -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, subject: str) -> str:
        self.git("add", ".")
        self.git("commit", "-qm", subject, "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def candidates(self, base: str, head: str = "HEAD") -> list[dict[str, Any]]:
        return prefix_candidates.candidates(self.repo, self.profile, base, head)

    def seed_pairs(self) -> str:
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage'\n")
        self.write("src/shop/grading/prefix_store.py", "RECORD = 'prefix'\n")
        self.write(
            "src/shop/readers.py",
            "from shop.grading import coverage_store, prefix_store\n",
        )
        return self.commit("Existing record stores and readers")

    def add_producers(self) -> str:
        self.write(
            "src/shop/grading/coverage_offline.py",
            "from .coverage_store import RECORD\n",
        )
        self.write("src/shop/grading/prefix_grade.py", "from . import prefix_store\n")
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage/1'\n")
        self.write("src/shop/grading/prefix_store.py", "RECORD = 'prefix/1'\n")
        return self.commit("Add both producers and update their stores")

    def test_two_producer_store_pairs_have_relationship_evidence(self) -> None:
        base = self.seed_pairs()
        self.add_producers()
        clusters = self.candidates(base)
        assert [cluster["prefix"] for cluster in clusters] == ["coverage", "prefix"]
        for cluster, producer in zip(
            clusters, ["coverage_offline", "prefix_grade"], strict=True
        ):
            prefix = cluster["prefix"]
            source = f"src/shop/grading/{producer}.py"
            store = f"src/shop/grading/{prefix}_store.py"
            assert cluster["folder"] == "src/shop/grading"
            assert cluster["members"] == sorted([source, store])
            assert cluster["added_or_moved"] == [source]
            assert cluster["internal_edges"] == [{"source": source, "target": store}]
            assert cluster["importers"] == {source: [], store: ["src/shop/readers.py"]}
            assert cluster["co_change"] == [
                {"members": sorted([source, store]), "count": 1}
            ]

    def test_existing_pairs_without_addition_or_move_are_not_candidates(self) -> None:
        self.seed_pairs()
        base = self.add_producers()
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage/2'\n")
        self.commit("Modify existing pair only")
        assert self.candidates(base) == []
        assert self.candidates(base, base) == []

    def test_moved_member_is_a_candidate(self) -> None:
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage'\n")
        self.write(
            "src/shop/incoming.py",
            "def produce():\n    return 'unique coverage producer payload'\n",
        )
        base = self.commit("Existing producer outside grading")
        (self.repo / "src/shop/incoming.py").rename(
            self.repo / "src/shop/grading/coverage_offline.py"
        )
        self.commit("Move producer beside its store")
        (cluster,) = self.candidates(base)
        assert cluster["prefix"] == "coverage"
        assert cluster["added_or_moved"] == ["src/shop/grading/coverage_offline.py"]

    def test_a_prefix_naming_the_folder_is_skipped_but_a_child_folder_is_not(
        self,
    ) -> None:
        self.write("src/shop/grading/coverage/records.py")
        self.write("src/shop/grading/coverage_store.py")
        self.write("src/shop/gradings/grading_store.py")
        base = self.commit("Existing owner folders")
        self.write("src/shop/grading/coverage_offline.py")
        self.write("src/shop/gradings/grading_producer.py")
        self.commit("Add prefixed siblings")
        assert [cluster["prefix"] for cluster in self.candidates(base)] == ["coverage"]

    def test_tests_init_modifiers_short_tokens_and_skipped_folders_make_no_group(
        self,
    ) -> None:
        base = self.commit("Empty base")
        for path in [
            "src/shop/test_coverage.py",
            "src/shop/test_prefix.py",
            "src/shop/coverage_test.py",
            "src/shop/prefix_test.py",
            "src/shop/__init__.py",
            "src/shop/init_store.py",
            "src/shop/base_reader.py",
            "src/shop/base_writer.py",
            "src/shop/io_reader.py",
            "src/shop/io_writer.py",
            "src/shop/tests/audit_writer.py",
            "src/shop/tests/audit_store.py",
            "src/shop/.cache/audit_writer.py",
            "src/shop/.cache/audit_store.py",
            "src/skill/SKILL.md",
            "src/skill/audit_writer.py",
            "src/skill/audit_store.py",
            "src/shop/conftest.py",
            "src/shop/conftest_store.py",
        ]:
            self.write(path)
        self.commit("Add tests and package marker")
        assert self.candidates(base) == []

    def test_the_leading_name_token_is_the_prefix(self) -> None:
        self.write("src/shop/cross_mode.py", "VALUE = 1\n")
        base = self.commit("Whole-stem sibling")
        self.write("src/shop/cross_mode_metrics.py", "from .cross_mode import VALUE\n")
        self.commit("Add metrics sibling")
        (cluster,) = self.candidates(base)
        assert cluster["prefix"] == "cross"
        assert cluster["members"] == [
            "src/shop/cross_mode.py",
            "src/shop/cross_mode_metrics.py",
        ]

    def test_single_member_and_partial_words_do_not_make_a_group(self) -> None:
        base = self.commit("Empty base")
        self.write("src/shop/prefix_grade.py")
        self.write("src/shop/prefixed_store.py")
        self.write("src/shop/standalone.py")
        self.commit("Add unrelated stems")
        assert self.candidates(base) == []

    def test_modified_group_is_not_activated_by_an_unrelated_addition(self) -> None:
        self.seed_pairs()
        base = self.add_producers()
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage/2'\n")
        self.write("src/shop/grading/unrelated.py")
        self.commit("Change existing pair and add unrelated member")
        assert self.candidates(base) == []

    def test_head_evidence_ignores_uncommitted_worktree(self) -> None:
        base = self.seed_pairs()
        head = self.add_producers()
        self.write("src/shop/grading/coverage_offline.py", "not valid python !!!")
        self.write("src/shop/grading/coverage/child.py")
        assert [cluster["prefix"] for cluster in self.candidates(base, head)] == [
            "coverage",
            "prefix",
        ]

    def test_documented_non_owned_sources_are_excluded(self) -> None:
        self.profile["checkers"] = {"check": {"exclude": ["vendor"]}}
        base = self.commit("Empty base")
        self.write("vendor/coverage_store.py")
        self.write("vendor/coverage_offline.py")
        self.commit("Vendor third-party pair")
        assert self.candidates(base) == []

    def test_run_prints_json_without_deterministic_violation(self) -> None:
        base = self.seed_pairs()
        self.add_producers()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            findings = prefix_candidates.run(self.repo, self.profile, ["--base", base])
        assert findings == []
        assert [cluster["prefix"] for cluster in json.loads(output.getvalue())] == [
            "coverage",
            "prefix",
        ]

    def test_standalone_main_accepts_repo_and_range(self) -> None:
        base = self.commit("Empty base")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = prefix_candidates.main([
                "--repo",
                str(self.repo),
                "--base",
                base,
                "--head",
                base,
            ])
        assert status == 0
        assert json.loads(output.getvalue()) == []

    def test_invalid_range_produces_a_finding_not_empty_candidates(self) -> None:
        self.commit("Empty base")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            findings = prefix_candidates.run(
                self.repo, self.profile, ["--base", "missing-ref"]
            )
        assert output.getvalue() == ""
        assert [finding.rule_id for finding in findings] == ["PREFIX.candidates"]


if __name__ == "__main__":
    unittest.main()
