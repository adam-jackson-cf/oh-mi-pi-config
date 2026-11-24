"""Committed package layouts for the reference runtime-data and entry-point placement checks."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands.structure import package_placement
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import catalogue_rules, remediations

pytestmark = pytest.mark.fast

FIXTURES = Path(__file__).resolve().parent / "fixtures/reference/structure"
CASES = json.loads((FIXTURES / "package-placement.json").read_text())["cases"]


class PackagePlacementTests(unittest.TestCase):
    def test_each_layout_reports_exactly_its_misplaced_files(self) -> None:
        for case in CASES:
            with self.subTest(case=case["name"]):
                profile = case.get("profile", {})
                found = apply_exceptions(
                    package_placement.package_file_findings(
                        case.get("paths", case["inventory"]), case["inventory"], profile
                    ),
                    profile,
                )
                assert sorted((item.rule_id, item.path) for item in found) == sorted(
                    tuple(row) for row in case["findings"]
                )

    def test_findings_name_the_package_and_the_way_out(self) -> None:
        inventory = [
            "src/shop/__init__.py",
            "src/shop/rules/policy.yaml",
            "src/shop/engine/__init__.py",
            "src/shop/engine/jobs/__init__.py",
            "src/shop/engine/jobs/__main__.py",
        ]
        data, entry = package_placement.package_file_findings(
            ["src/shop/rules/policy.yaml", "src/shop/engine/jobs/__main__.py"],
            inventory,
            {},
        )
        assert data.render() == (
            "src/shop/rules/policy.yaml: STRUCT.runtime-data-placement: "
            "runtime data file inside code folder src/shop/; move it to a root data "
            "folder outside the code and read it from there"
        )
        assert entry.render() == (
            "src/shop/engine/jobs/__main__.py: STRUCT.entry-point-placement: "
            "entry module __main__.py sits 2 folders deep in package src/shop/, below "
            "its top tier; move it to src/shop/ or a folder directly under it, or record "
            "an owner-approved guardrails.exceptions entry for an internal tool"
        )
        for rule in (package_placement.RUNTIME_DATA, package_placement.ENTRY_POINT):
            assert remediations()[rule].render().startswith("root cause: ")

    def test_catalogue_contracts_state_the_implemented_lists(self) -> None:
        rules = {rule["id"]: rule["contract"] for rule in catalogue_rules()}
        data = rules[package_placement.RUNTIME_DATA]
        assert data["data_suffixes"] == sorted(package_placement.DATA_SUFFIXES)
        assert data["bundle_manifests"] == sorted(package_placement.BUNDLE_MANIFESTS)
        entry = rules[package_placement.ENTRY_POINT]
        assert entry["entry_modules"] == sorted(package_placement.ENTRY_MODULES)


if __name__ == "__main__":
    unittest.main()
