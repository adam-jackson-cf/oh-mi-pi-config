"""House-style verdicts and the structure, naming and path checkers of the guardrails reference."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands.structure import import_graph
from guardrails.commands.structure.import_direction import direction_findings
from guardrails.context.config import house_style
from guardrails.foundation import conventions

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

REFERENCE = ROOT / "agent/extensions/dynaflows/engine"

FIXTURES = ROOT / "tests/fixtures/reference/structure"

VERDICTS = json.loads((FIXTURES / "house-style-verdicts.json").read_text())


class HouseStyleVerdictTests(unittest.TestCase):
    """The shared, stack-neutral verdict set every house-style reader must reproduce."""

    def test_fixture_targets_the_shipped_version_and_sources_are_recorded(self) -> None:
        data = house_style.load_house_style()
        assert VERDICTS["house_style_version"] == data["version"]
        assert len(data["sources"]) >= 5
        assert data["banned_words"] == sorted(set(data["banned_words"]))
        assert re.search(r"^[0-9a-f]{64}$", house_style.digest())

    def test_banned_words(self) -> None:
        for word in VERDICTS["banned_words"]["banned"]:
            assert house_style.is_banned_word(word), word
        for word in VERDICTS["banned_words"]["allowed"]:
            assert not house_style.is_banned_word(word), word

    def test_casing(self) -> None:
        for case in VERDICTS["casing"]:
            with self.subTest(case=case):
                assert (
                    house_style.matches_casing(case["name"], case["casing"])
                    == case["matches"]
                )

    def test_versions(self) -> None:
        for case in VERDICTS["versions"]:
            with self.subTest(case=case):
                problem = house_style.version_problem(
                    case["segment"],
                    last=case["last"],
                    standalone_allowed=case["standalone_allowed"],
                )
                assert (problem is not None) == case["violates"]

    def test_test_names(self) -> None:
        for case in VERDICTS["test_names"]:
            with self.subTest(case=case):
                problems = house_style.test_name_problems(case["name"])
                assert bool(problems) == case["violates"], problems

    def test_children_repeat_parent_by_whole_token_sequence(self) -> None:
        repeated_paths = (
            "guardrails/guardrails.py",
            "guardrails/omp_guardrails/entry.py",
            "guardrails/guardrails-core/entry.py",
            "guardrails/omp.guardrails/entry.py",
            "guardrails/ompGuardrails/entry.ts",
            "guardrails/GuardrailsCore/entry.ts",
            "HTTPServer/omp_http_server/entry.py",
            "audit-log/omp_audit_log_core/entry.py",
            "audit.log/ompAuditLog/entry.py",
            "guardrails/_guardrails_/entry.py",
            "guardrails/guardrails.test.py",
            "guardrails/.guardrails/entry.py",
            "guardrails/omp_guardrails.py",
            "guardrails/guardrails-core.ts",
            "guardrails/Guardrails.py",
            "guardrails/.guardrails",
            "harness/harness-tokens.js",
            "performance/test_performance_x.py",
            "base/analyzer_base.py",
            "init/__init__.js",
            "main/__main__.ts",
        )
        for path in repeated_paths:
            with self.subTest(path=path):
                assert (
                    house_style.child_repeats_parent(tuple(path.split("/"))) is not None
                )
        allowed_paths = (
            "rail/guardrails/entry.py",
            "guardrails/guardrailsish/entry.py",
            "audit-log/audit/entry.py",
            "audit-log/log_audit/entry.py",
            "audit-log/audit_event_log/entry.py",
            "guardrails/role.guardrails.py",
            "audit.log/audit.py",
            "init/__init__.py",
            "main/__main__.py",
            "foo/foo.go",
            "foo/foo_test.go",
            "___/role.py",
            "entry.py",
        )
        for path in allowed_paths:
            with self.subTest(path=path):
                assert house_style.child_repeats_parent(tuple(path.split("/"))) is None
        assert (
            house_style.child_repeats_parent((
                "guardrails",
                "omp_guardrails",
                "guardrails.py",
            ))
            == "omp_guardrails"
        )

    def test_test_roots(self) -> None:
        expected = VERDICTS["test_roots"]
        assert conventions.placement("test_sample.py")["root"] == expected["root"]
        assert (
            conventions.placement("test_sample.py")["fixtures_dir"]
            == expected["fixtures_dir"]
        )
        assert expected["python"] in conventions.test_patterns("test_sample.py")
        assert expected["typescript"] in conventions.test_patterns("sample.test.ts")


ENGINE_SOURCE_ROOT = "agent/extensions/dynaflows/engine/guardrails"
ENGINE_LAYERS = {
    "structure": {
        "tiers": ["foundation", "context", "toolkit", "commands"],
        "import_direction": {
            "package": "guardrails",
            "source_root": ENGINE_SOURCE_ROOT,
        },
    }
}


class EngineLayerTests(unittest.TestCase):
    def test_engine_modules_import_only_their_own_or_a_lower_layer(self) -> None:
        paths = sorted(
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / ENGINE_SOURCE_ROOT).rglob("*.py")
        )
        found = [
            *import_graph.run(ROOT, ENGINE_LAYERS, [], inventory=paths),
            *(
                finding
                for path in paths
                for finding in direction_findings(ROOT, path, ENGINE_LAYERS)
            ),
        ]
        assert found == []


if __name__ == "__main__":
    unittest.main()
