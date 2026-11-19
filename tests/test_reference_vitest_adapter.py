"""Vitest action routing and strict evidence conversion, without network/tool installation."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine"))


from guardrails.adapters import vitest
from guardrails.adapters.vitest import mutation, tiers
from guardrails.config.profile import GUARDRAILS_SECTIONS
from guardrails.test_evidence.mutation import load_report

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/dynaflows/engine"


class VitestAdapterTests(unittest.TestCase):
    def test_registered_profile_section_and_all_actions(self) -> None:
        assert "adapter_vitest" in GUARDRAILS_SECTIONS
        expected = {"lint", "tiers", "junit-args", "guard-evidence", "affected", "mutation-tool", "mutation-report", "conformance"}
        parser = vitest.parser()
        choices = next(action.choices for action in parser._actions if action.dest == "action")
        assert choices is not None
        assert set(choices) == expected

    def test_outputs_cannot_be_inside_target(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            for action, extra in [("tiers", []), ("junit-args", ["--tier", "t1"]), ("mutation-report", [])]:
                findings = vitest.run(repo, {}, [action, "--out", str(repo / "evidence"), *extra])
                assert [item.rule_id for item in findings] == ["GUARDRAILS.output-outside-repo"]

    def test_native_selectors_and_ids(self) -> None:
        assert tiers.selector_arguments('--config "test config.ts" --testNamePattern "total > sums"') == ["--config", "test config.ts", "--testNamePattern", "total > sums"]
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            entries = [{"file": str(repo / "tests/total.test.ts"), "name": "total > sums"}]
            assert tiers.collected_ids(repo, entries) == ["tests/total.test.ts::total > sums"]
            with self.assertRaises(ValueError):
                tiers.collected_ids(repo, entries * 2)
            with self.assertRaises(ValueError):
                tiers.collected_ids(repo, [{"file": "../foreign.test.ts", "name": "foreign"}])
            with self.assertRaises(ValueError):
                tiers.collected_ids(repo, {})

    def test_missing_runner_and_selector_are_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            assert tiers.collect_tier(repo, {}, "t1", repo.parent / "evidence")
            with self.assertRaises(ValueError):
                tiers.command(repo, {})
            with self.assertRaises(ValueError):
                tiers.command(repo, {"guardrails": {"adapter_vitest": {"command": "vitest"}}})

    def test_stryker_normalizes_every_status_and_scopes_ids(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            repo = root / "repo"
            repo.mkdir()
            source, out = root / "stryker.json", root / "normalised.json"
            mutants = [{"id": str(index), "location": {"start": {"line": 2, "column": 1}, "end": {"line": 4, "column": 2}}, "status": status} for index, status in enumerate(mutation.STATUSES)]
            source.write_text(json.dumps({"schemaVersion": "2", "files": {"src/total.ts": {"mutants": mutants}}}))
            assert mutation.write_report(repo, source, out) == []
            parsed = load_report(out)
            assert len(parsed) == len(mutation.STATUSES)
            assert {item.status for item in parsed} == {"killed", "survived", "no_tests", "timeout", "incomplete"}
            assert all(item.path == "src/total.ts" for item in parsed)
            assert all(item.id.startswith("src/total.ts::") for item in parsed)
            mutants[0]["status"] = "Unexpected"
            source.write_text(json.dumps({"files": {"src/total.ts": {"mutants": mutants}}}))
            assert mutation.write_report(repo, source, out)

    def test_mutation_tool_names_exact_version(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert vitest.run(Path.cwd(), {}, ["mutation-tool"]) == []
        assert f"{mutation.TOOL}@{mutation.VERSION}" in out.getvalue()


if __name__ == "__main__":
    unittest.main()
