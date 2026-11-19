"""Committed sibling fixtures for the reference PREFIX.candidates pre-check."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/dynaflows/engine"
sys.path.insert(0, str(REFERENCE))

from guardrails.structure import prefix_candidates  # noqa: E402


class PrefixCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-prefix-candidates-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        self.record = Path(scratch.name) / "record"
        self.repo.mkdir()
        self.record.mkdir()
        self.profile: dict = {
            "structure": {"import_direction": {"source_root": "src/shop", "package": "shop"}}
        }
        (self.record / "profile.md").write_text("---\n" + json.dumps(self.profile) + "\n---\n")
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()

    def write(self, path: str, text: str = "") -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, subject: str) -> str:
        self.git("add", ".")
        self.git("commit", "-qm", subject, "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def candidates(self, base: str, head: str = "HEAD") -> list[dict]:
        return prefix_candidates.candidates(self.repo, self.profile, base, head)

    def seed_pairs(self) -> str:
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage'\n")
        self.write("src/shop/grading/prefix_store.py", "RECORD = 'prefix'\n")
        self.write("src/shop/readers.py", "from shop.grading import coverage_store, prefix_store\n")
        return self.commit("Existing record stores and readers")

    def add_producers(self) -> str:
        self.write("src/shop/grading/coverage_offline.py", "from .coverage_store import RECORD\n")
        self.write("src/shop/grading/prefix_grade.py", "from . import prefix_store\n")
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage/1'\n")
        self.write("src/shop/grading/prefix_store.py", "RECORD = 'prefix/1'\n")
        return self.commit("Add both producers and update their stores")

    def test_two_producer_store_pairs_have_relationship_evidence(self) -> None:
        base = self.seed_pairs()
        self.add_producers()
        clusters = self.candidates(base)
        self.assertEqual([cluster["prefix"] for cluster in clusters], ["coverage", "prefix"])
        for cluster, producer in zip(clusters, ["coverage_offline", "prefix_grade"]):
            prefix = cluster["prefix"]
            source = f"src/shop/grading/{producer}.py"
            store = f"src/shop/grading/{prefix}_store.py"
            self.assertEqual(cluster["folder"], "src/shop/grading")
            self.assertEqual(cluster["members"], sorted([source, store]))
            self.assertEqual(cluster["added_or_moved"], [source])
            self.assertEqual(cluster["internal_edges"], [{"source": source, "target": store}])
            self.assertEqual(cluster["importers"], {source: [], store: ["src/shop/readers.py"]})
            self.assertEqual(cluster["co_change"], [{"members": sorted([source, store]), "count": 1}])

    def test_existing_pairs_without_addition_or_move_are_not_candidates(self) -> None:
        self.seed_pairs()
        base = self.add_producers()
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage/2'\n")
        self.commit("Modify existing pair only")
        self.assertEqual(self.candidates(base), [])
        self.assertEqual(self.candidates(base, base), [])

    def test_moved_member_is_a_candidate(self) -> None:
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage'\n")
        self.write("src/shop/incoming.py", "def produce():\n    return 'unique coverage producer payload'\n")
        base = self.commit("Existing producer outside grading")
        (self.repo / "src/shop/incoming.py").rename(self.repo / "src/shop/grading/coverage_offline.py")
        self.commit("Move producer beside its store")
        cluster, = self.candidates(base)
        self.assertEqual(cluster["prefix"], "coverage")
        self.assertEqual(cluster["added_or_moved"], ["src/shop/grading/coverage_offline.py"])

    def test_existing_child_folder_and_parent_name_are_skipped(self) -> None:
        self.write("src/shop/grading/coverage/records.py")
        self.write("src/shop/grading/coverage_store.py")
        self.write("src/shop/grading/grading_store.py")
        base = self.commit("Existing owner folders")
        self.write("src/shop/grading/coverage_offline.py")
        self.write("src/shop/grading/grading_producer.py")
        self.commit("Add redundant prefixed siblings")
        self.assertEqual(self.candidates(base), [])

    def test_tests_and_init_files_do_not_join_groups(self) -> None:
        base = self.commit("Empty base")
        for path in [
            "src/shop/test_coverage.py", "src/shop/test_prefix.py",
            "src/shop/coverage_test.py", "src/shop/prefix_test.py",
            "src/shop/__init__.py", "src/shop/init_store.py",
        ]:
            self.write(path)
        self.commit("Add tests and package marker")
        self.assertEqual(self.candidates(base), [])

    def test_whole_stem_member_counts_and_longest_words_win(self) -> None:
        self.write("src/shop/cross_mode.py", "VALUE = 1\n")
        base = self.commit("Whole-stem sibling")
        self.write("src/shop/cross_mode_metrics.py", "from .cross_mode import VALUE\n")
        self.commit("Add metrics sibling")
        cluster, = self.candidates(base)
        self.assertEqual(cluster["prefix"], "cross_mode")
        self.assertEqual(cluster["members"], ["src/shop/cross_mode.py", "src/shop/cross_mode_metrics.py"])

    def test_single_member_and_partial_words_do_not_make_a_group(self) -> None:
        base = self.commit("Empty base")
        self.write("src/shop/prefix_grade.py")
        self.write("src/shop/prefixed_store.py")
        self.write("src/shop/standalone.py")
        self.commit("Add unrelated stems")
        self.assertEqual(self.candidates(base), [])

    def test_modified_group_is_not_activated_by_an_unrelated_addition(self) -> None:
        self.seed_pairs()
        base = self.add_producers()
        self.write("src/shop/grading/coverage_store.py", "RECORD = 'coverage/2'\n")
        self.write("src/shop/grading/unrelated.py")
        self.commit("Change existing pair and add unrelated member")
        self.assertEqual(self.candidates(base), [])

    def test_head_evidence_ignores_uncommitted_worktree(self) -> None:
        base = self.seed_pairs()
        head = self.add_producers()
        self.write("src/shop/grading/coverage_offline.py", "not valid python !!!")
        self.write("src/shop/grading/coverage/child.py")
        self.assertEqual([cluster["prefix"] for cluster in self.candidates(base, head)], ["coverage", "prefix"])

    def test_documented_non_owned_sources_are_excluded(self) -> None:
        self.profile["guardrails"] = {"check": {"exclude": ["vendor"]}}
        base = self.commit("Empty base")
        self.write("vendor/coverage_store.py")
        self.write("vendor/coverage_offline.py")
        self.commit("Vendor third-party pair")
        self.assertEqual(self.candidates(base), [])

    def test_run_prints_json_without_deterministic_violation(self) -> None:
        base = self.seed_pairs()
        self.add_producers()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            findings = prefix_candidates.run(self.repo, self.profile, ["--base", base])
        self.assertEqual(findings, [])
        self.assertEqual([cluster["prefix"] for cluster in json.loads(output.getvalue())], ["coverage", "prefix"])

    def test_standalone_main_accepts_repo_record_and_range(self) -> None:
        base = self.commit("Empty base")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = prefix_candidates.main([
                "--repo", str(self.repo), "--record", str(self.record),
                "--base", base, "--head", base,
            ])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), [])

    def test_invalid_range_produces_a_finding_not_empty_candidates(self) -> None:
        self.commit("Empty base")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            findings = prefix_candidates.run(self.repo, self.profile, ["--base", "missing-ref"])
        self.assertEqual(output.getvalue(), "")
        self.assertEqual([finding.rule_id for finding in findings], ["PREFIX.candidates"])


if __name__ == "__main__":
    unittest.main()
