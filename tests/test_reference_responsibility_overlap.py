"""Committed repositories for the reference STRUCT.overlapping-responsibilities check."""

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

from guardrails.commands.structure import responsibility_overlap as overlap
from guardrails.foundation.findings import catalogue_rules, remediations

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parent
        / "fixtures/reference/structure/responsibility-overlap.json"
    ).read_text()
)
COPIED = FIXTURE["base"]["src/billing/report.py"]


@pytest.mark.fast
class DefinitionTests(unittest.TestCase):
    def test_docstrings_and_comments_do_not_change_the_features(self) -> None:
        annotated = COPIED.replace(
            "    totals = {}\n",
            '    """Totals per customer."""\n    totals = {}  # running totals\n',
        )
        plain = overlap.definitions("a", "a/x.py", COPIED)
        noted = overlap.definitions("b", "b/x.py", annotated)
        assert [item.grams for item in plain] == [item.grams for item in noted]
        assert [item.name for item in plain] == ["summarise_orders"]

    def test_short_and_small_definitions_are_dropped(self) -> None:
        short = "def total(a, b):\n    c = a + b\n    d = c * 2\n    return d\n"
        small = "def one():\n    pass\n    pass\n    pass\n    return 1\n"
        assert overlap.definitions("a", "a/x.py", short) == []
        assert overlap.definitions("a", "a/x.py", small) == []

    def test_the_cutoff_and_owner_evidence_live_in_the_catalogue(self) -> None:
        rule = next(item for item in catalogue_rules() if item["id"] == overlap.RULE)
        assert overlap.pair_score_cutoff() == rule["contract"]["pair_score_cutoff"]
        evidence = rule["contract"]["evidence"]
        assert (evidence["accepted_by"], evidence["accepted_on"]) == (
            "owner",
            "2026-11-23",
        )
        assert evidence["held_out_precision"] == 0.74
        assert remediations()[overlap.RULE].render().startswith("root cause: ")


@pytest.mark.seam
class OverlapCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-responsibility-overlap-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def repository(self, case: dict[str, Any]) -> tuple[Path, str, str]:
        repo = self.scratch / str(len(list(self.scratch.iterdir())))
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Fixture")
        self.git(repo, "config", "user.email", "fixture@example.invalid")
        base = self.commit(repo, {**FIXTURE["base"], **case.get("base_extra", {})})
        return repo, base, self.commit(repo, case["head"])

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

    def test_each_repository_reports_exactly_its_overlapping_folders(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                repo, base, head = self.repository(case)
                found = overlap.run(
                    repo, case.get("profile", {}), ["--base", base, "--head", head]
                )
                assert sorted((item.rule_id, item.path) for item in found) == sorted(
                    tuple(row) for row in case["findings"]
                )

    def test_the_finding_names_both_folders_and_the_closest_definitions(self) -> None:
        repo, base, head = self.repository(FIXTURE["cases"][0])
        (finding,) = overlap.run(repo, {}, ["--base", base, "--head", head])
        assert finding.render() == (
            "src/shipping/: STRUCT.overlapping-responsibilities: shares code with "
            "src/billing/: src/shipping/summary.py summarise_orders and "
            "src/billing/report.py summarise_orders have 3-gram similarity 1.000 at "
            "or above 0.336; move the shared logic into one shared mechanism both "
            "units use, or record an owner-approved guardrails.exceptions entry "
            "{rule, path, reason}"
        )

    def test_unreadable_commits_are_a_finding(self) -> None:
        repo, base, _ = self.repository(FIXTURE["cases"][0])
        (finding,) = overlap.run(repo, {}, ["--base", base, "--head", "missing"])
        assert (finding.rule_id, finding.path) == (overlap.RULE, ".")


if __name__ == "__main__":
    unittest.main()
