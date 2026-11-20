"""Per-repo record resolution against real git clones, worktrees and temp records."""

import sys
import unittest
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent/lib"))
import repo_record

pytestmark = pytest.mark.fast

ORIGINS = {
    "https://github.com/Owner-1/Repo.Name": "owner-1/repo.name",
    "https://github.com/Owner-1/Repo.Name.git": "owner-1/repo.name",
    "git@github.com:Owner-1/Repo_Name": "owner-1/repo_name",
    "git@github.com:Owner-1/Repo_Name.git": "owner-1/repo_name",
    "ssh://git@github.com/Owner-1/repo-name": "owner-1/repo-name",
    "ssh://git@github.com/Owner-1/repo-name.git\n": "owner-1/repo-name",
}

REFUSED = [
    "https://gitlab.com/owner/name.git",
    "https://github.com/owner",
    "https://github.com/owner/name/extra",
    "https://github.com/../name",
    "git@github.com:owner/..",
    "file:///tmp/owner/name",
    "",
]

# A complete gate.json; tests vary one field at a time.
GATE = {
    "schemaVersion": 2,
    "checks": {"commit": ["make", "gate"]},
    "dynaflows": {"enabled": True},
}


def gate(**fields: object) -> dict[str, Any]:
    """GATE with `fields` replacing its values; a None value removes the field."""
    merged = {**GATE, **fields}
    return {key: value for key, value in merged.items() if value is not None}


class ParseOrigin(unittest.TestCase):
    def test_each_github_form_maps_to_lowercase_owner_and_name(self) -> None:
        for url, slug in ORIGINS.items():
            with self.subTest(url=url):
                assert repo_record.parse_origin(url) == slug

    def test_non_github_or_malformed_origins_have_no_slug(self) -> None:
        for url in REFUSED:
            with self.subTest(url=url):
                assert repo_record.parse_origin(url) is None


class GateReason(unittest.TestCase):
    def test_complete_gates_with_optional_fields_are_accepted(self) -> None:
        for valid in (
            GATE,
            gate(agentMerge="disabled"),
            gate(
                checks={
                    "commit": ["c"],
                    "ci": ["true"],
                    "audit": ["bash", "scripts/audit-dependencies.sh"],
                    "format": ["ruff", "format"],
                    "profile": ["p"],
                },
                ci={"requiredContext": "gate", "runner": "ci-gate"},
                dynaflows={"enabled": False},
            ),
        ):
            with self.subTest(gate=valid):
                assert repo_record.gate_reason(valid) is None

    def test_each_invalid_gate_names_its_reason(self) -> None:
        cases = [
            ({"schemaVersion": True}, "unsupported gate schemaVersion"),
            (gate(schemaVersion=3), "unsupported gate schemaVersion"),
            (gate(checks=None), "checks must be an object with checks.commit"),
            (gate(checks={"ci": ["true"]}), "checks.commit must be argv"),
            (gate(checks={"commit": []}), "checks.commit must be argv"),
            (gate(checks={"commit": ["c"], "ci": None}), "checks.ci must be argv"),
            (
                gate(checks={"commit": ["c"], "format": "ruff format"}),
                "checks.format must be argv",
            ),
            (
                gate(checks={"commit": ["c"], "profile": []}),
                "checks.profile must be argv",
            ),
            (
                gate(checks={"commit": ["c"], "audit": ["c", " "]}),
                "checks.audit must be argv",
            ),
            (
                gate(checks={"commit": ["c"], "lint": ["x"]}),
                "unknown field checks.lint must be removed",
            ),
            (gate(unknownField=3), "unknown field unknownField must be removed"),
            (
                gate(unknownField=3, extraField=5),
                "unknown field extraField, unknownField must be removed",
            ),
            (gate(otherField="github"), "unknown field otherField must be removed"),
            (gate(agentMerge=False), "agentMerge must be allowed or disabled"),
            (gate(ci="gate"), "ci must declare nonblank requiredContext and runner"),
            (
                gate(ci={"requiredContext": "gate"}),
                "ci must declare nonblank requiredContext and runner",
            ),
            (
                gate(ci={"requiredContext": " ", "runner": "ci-gate"}),
                "ci must declare nonblank requiredContext and runner",
            ),
            (
                gate(ci={"requiredContext": "gate", "runner": 1}),
                "ci must declare nonblank requiredContext and runner",
            ),
            (
                gate(ownerOnlyPaths=[]),
                "unknown field ownerOnlyPaths must be removed",
            ),
            (
                gate(
                    ownerOnlyPaths=["scripts/checks/dependency-audit-exceptions.json"]
                ),
                "unknown field ownerOnlyPaths must be removed",
            ),
        ]
        cases += [
            (gate(dynaflows=switch), 'dynaflows must be {"enabled": true|false}')
            for switch in (None, {}, {"enabled": 1}, {"enabled": "true"}, True)
        ]
        for invalid, reason in cases:
            with self.subTest(gate=invalid):
                assert repo_record.gate_reason(invalid) == reason


if __name__ == "__main__":
    unittest.main()
