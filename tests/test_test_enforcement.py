"""Test rules carry their own guidance in the catalogue remediation they print."""

import json
import unittest
from pathlib import Path

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = (
    ROOT / "agent/extensions/dynaflows/engine/guardrails/data/guardrail-rules.json"
)

# Each test rule's emitting subcommands and the guidance facts its remediation states, so a
# finding is actionable without opening a skill.
RULE_FACTS = {
    "PROFILE.complete": (
        ("config-check",),
        ("every required config value", "approval reference"),
    ),
    "TEST.fast-tier-isolation": (
        ("guard-run",),
        ("setup and teardown", "never starts a subprocess", "network connection"),
    ),
    "TEST.tier-selectors": (
        ("collection-check",),
        ("disjoint", "exactly one tier", "collect-only"),
    ),
    "TEST.placement": (
        ("path-check",),
        ("ownership area", "fixtures and support"),
    ),
    "NAME.test-files": (
        ("naming",),
        ("ticket, version and date tokens", "owning module or feature"),
    ),
    "NAME.conventions": (
        ("naming",),
        ("sibling naming patterns",),
    ),
    "TEST.property-seed": (
        ("adapter",),
        ("seed and shrink path", "named example"),
    ),
    "TEST.mutation-sensitivity": (
        ("adapter", "mutation-check"),
        ("nightly T3", "changed code", "surviving, untested or unfinished"),
    ),
    "TEST.no-self-mocking": (
        ("adapter",),
        ("runtime-patch", "production adapter"),
    ),
    "TEST.no-test-hooks": (
        ("adapter",),
        ("test-only flags", "environment variables", "production inputs"),
    ),
    "TEST.subprocess-timeouts": (
        ("python-test-hygiene",),
        (
            "synchronous subprocess",
            "communicate",
            "positive timeout",
            "async",
            "shared fixtures",
        ),
    ),
    "TEST.user-data-guard": (
        ("guard-run",),
        ("HOME and XDG", "temporary directory", "real user home", "failure"),
    ),
    "TEST.coverage-ratchet": (
        ("check", "diff-check"),
        (
            "no coverage exclusions",
            "no-cover pragmas",
            "lowering a floor",
            "owner approval",
        ),
    ),
    "TEST.growth.justified": (
        ("check", "diff-check"),
        ("at least two meaningful words", "parameter rows", "up to 20", "more than 2"),
    ),
    "TEST.duplicate-path": (
        ("check", "diff-check"),
        ("normalized call path", "normalized assertion", "merge"),
    ),
    "TEST.budget.per-test-time": (
        ("budget-check",),
        ("at least five", "same machine", "owner approval", "CI does not measure"),
    ),
    "TEST.budget.fast-tier-time": (
        ("budget-check",),
        ("at least five", "same machine", "owner approval", "measurements"),
    ),
    "TEST.identity-artefacts": (
        ("check", "diff-check"),
        ("no production file changed", "identity-bearing artefact", "byte-identical"),
    ),
    "TEST.failure-propagation": (
        ("report-check", "collection-check"),
        (
            "missing, skipped, timed out",
            "infrastructure",
            "collected zero tests",
            "failure",
        ),
    ),
    "TEST.no-evasion": (
        ("adapter", "report-check", "diff-check"),
        (
            "unapproved skip, xfail",
            "focus/only",
            "flaky/retry",
            "rerun until green",
            "raise a timeout",
        ),
    ),
    "TEST.golden-change-record": (
        ("check", "diff-check"),
        ("every changed golden file", "change record", "first divergent step"),
    ),
    "SURFACE.lever-isolation": (
        ("lever-selftest",),
        (
            "doctor-failure marker",
            "only its own process group",
            "per-instance ports and data",
            "leaves no processes or ports",
            "keeps the evidence directory after cleanup",
        ),
    ),
    "SURFACE.feature-map": (
        ("surface-lint",),
        (
            "one feature-map document per feature",
            "four H2 sections",
            "Sub-features",
            "How to get to it (user POV)",
            "Driving it with",
            "Gotchas",
            "stale link",
        ),
    ),
    "SURFACE.journeys": (
        ("surface-lint",),
        ("journey file pattern", "test runner"),
    ),
}


class TestEnforcementTests(unittest.TestCase):
    def test_each_test_rule_remediation_carries_its_own_guidance(self) -> None:
        rules = {
            row["id"]: row
            for row in json.loads(CATALOGUE.read_text(encoding="utf-8"))["rules"]
        }
        for rule, (subcommands, facts) in RULE_FACTS.items():
            with self.subTest(rule=rule):
                row = rules[rule]
                assert set(subcommands) <= {
                    item["subcommand"] for item in row["reference"]
                }
                remediation = row["remediation"]
                assert remediation["root_cause"].strip()
                assert remediation["fix"].strip()
                text = " ".join(remediation.values()).casefold()
                for fact in facts:
                    assert fact.casefold() in text, fact

    def test_no_catalogue_remediation_points_at_a_skill(self) -> None:
        rules = json.loads(CATALOGUE.read_text(encoding="utf-8"))["rules"]
        pointing = [
            row["id"]
            for row in rules
            if any("skill://" in text for text in row["remediation"].values())
        ]
        assert pointing == []


if __name__ == "__main__":
    unittest.main()
