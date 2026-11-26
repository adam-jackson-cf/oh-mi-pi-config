"""Committed repositories for the STRUCT.overlapping-responsibilities check."""

from __future__ import annotations

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

from guardrails.commands.structure_naming import responsibility_overlap as overlap
from guardrails.foundation.findings import catalogue_rules, remediations

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parent
        / "fixtures/structure/responsibility-overlap.json"
    ).read_text()
)
COPIED = FIXTURE["base"]["src/billing/report.py"]
SCRIPT_COPY = """export function summariseOrders(orders: Order[], customer: string) {
  const totals = new Map<string, number>();
  for (const order of orders) {
    if (order.customer === customer) {
      totals.set(order.sku, (totals.get(order.sku) ?? 0) + order.amount);
    }
  }
  return [...totals.entries()].sort((a, b) => a[1] - b[1]);
}

const short = (a: number) => a + 1;

export class Ledger {
  balance(entries: Entry[]): number {
    let total = 0;
    for (const entry of entries) {
      total += entry.credit ? entry.amount : -entry.amount;
    }
    return total;
  }
}
"""
GO_COPY = """package billing

func SummariseOrders(orders []Order, customer string) map[string]int {
\ttotals := map[string]int{}
\tfor _, order := range orders {
\t\tif order.Customer == customer {
\t\t\ttotals[order.SKU] += order.Amount
\t\t}
\t}
\treturn totals
}

func (l *Ledger) Balance(entries []Entry) int {
\ttotal := 0
\tfor _, entry := range entries {
\t\tif entry.Credit {
\t\t\ttotal += entry.Amount
\t\t}
\t}
\treturn total
}
"""


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

    def test_methods_are_definitions_named_by_their_class(self) -> None:
        method = "".join(
            "    " + line if line else line for line in COPIED.splitlines(keepends=True)
        ).replace("def summarise_orders(", "def summarise(self, ")
        source = "class Report:\n    kind = 1\n\n" + method
        assert [item.name for item in overlap.definitions("a", "a/x.py", source)] == [
            "Report",
            "Report.summarise",
        ]

    def test_unparseable_source_has_no_definitions(self) -> None:
        assert overlap.definitions("a", "a/x.py", "def broken(:\n    pass\n") == []
        assert overlap.definitions("a", "a/x.ts", "function broken( {\n") == []

    def test_script_and_go_definitions_follow_the_language_definitions(self) -> None:
        script = overlap.definitions("a", "a/x.ts", SCRIPT_COPY)
        go = overlap.definitions("a", "a/x.go", GO_COPY)
        assert [(item.language, item.name) for item in script + go] == [
            ("typescript", "summariseOrders"),
            ("typescript", "Ledger"),
            ("typescript", "Ledger.balance"),
            ("go", "SummariseOrders"),
            ("go", "Ledger.Balance"),
        ]

    def test_units_of_different_languages_are_never_compared(self) -> None:
        (python,) = overlap.definitions("a", "a/x.py", COPIED)
        script = overlap.Definition("typescript", "b", "b/x.ts", "f", python.grams)
        twin = overlap.Definition("python", "b", "b/x.py", "f", python.grams)
        changed = {"python": {"a"}, "typescript": {"b"}}
        cutoffs = {"python": 0.336, "typescript": 0.336}
        assert overlap.overlap_findings([python, script], changed, cutoffs, {}) == []
        (found,) = overlap.overlap_findings(
            [python, script, twin], changed, cutoffs, {}
        )
        assert (found.path, "b/x.py f" in found.message) == ("a/", True)

    def test_the_cutoff_is_per_language_and_the_evidence_lives_in_the_catalogue(
        self,
    ) -> None:
        assert overlap.pair_score_cutoffs() == {
            "python": 0.336,
            "typescript": None,
            "go": None,
        }
        assert overlap.measured_cutoffs() == {"python": 0.336}
        rule = next(item for item in catalogue_rules() if item["id"] == overlap.RULE)
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
                    repo, case.get("config", {}), ["--base", base, "--head", head]
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
            "units use, or record an owner-approved exceptions entry "
            "{rule, path, reason}"
        )

    def test_a_language_without_a_cutoff_does_not_run_but_yields_candidates(
        self,
    ) -> None:
        files = {
            "src/billing/report.ts": SCRIPT_COPY,
            "src/shipping/summary.ts": SCRIPT_COPY,
            "src/billing/report.go": GO_COPY,
            "src/shipping/summary.go": GO_COPY,
        }
        repo, base, head = self.repository({"head": files})
        assert overlap.run(repo, {}, ["--base", base, "--head", head]) == []
        for language, suffix in (("typescript", "ts"), ("go", "go")):
            with self.subTest(language=language):
                pairs = overlap.overlap_candidates(repo, head, language, [])
                assert [
                    (pair.score, pair.first.file, pair.second.file)
                    for pair in pairs
                    if pair.score == 1.0
                ] == [
                    (
                        1.0,
                        f"src/billing/report.{suffix}",
                        f"src/shipping/summary.{suffix}",
                    )
                ]
                assert {pair.first.language for pair in pairs} == {language}

    def test_unreadable_commits_are_a_finding(self) -> None:
        repo, base, _ = self.repository(FIXTURE["cases"][0])
        (finding,) = overlap.run(repo, {}, ["--base", base, "--head", "missing"])
        assert (finding.rule_id, finding.path) == (overlap.RULE, ".")


if __name__ == "__main__":
    unittest.main()
