"""Vitest action routing and strict evidence conversion, without network/tool installation."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/dynaflows/guardrails"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails.adapters import vitest
from omp_guardrails.adapters.vitest import mutation, tiers
from omp_guardrails.config.profile import GUARDRAILS_SECTIONS
from omp_guardrails.test_evidence.mutation import load_report


class VitestAdapterTests(unittest.TestCase):
    def test_registered_profile_section_and_all_actions(self):
        self.assertIn("adapter_vitest", GUARDRAILS_SECTIONS)
        expected = {"lint", "tiers", "junit-args", "guard-evidence", "affected", "mutation-tool", "mutation-report", "conformance"}
        parser = vitest.parser()
        choices = next(action.choices for action in parser._actions if action.dest == "action")
        self.assertEqual(set(choices), expected)

    def test_outputs_cannot_be_inside_target(self):
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            for action, extra in [("tiers", []), ("junit-args", ["--tier", "t1"]), ("mutation-report", [])]:
                findings = vitest.run(repo, {}, [action, "--out", str(repo / "evidence"), *extra])
                self.assertEqual([item.rule_id for item in findings], ["GUARDRAILS.output-outside-repo"])

    def test_native_selectors_and_ids(self):
        self.assertEqual(tiers.selector_arguments('--config "test config.ts" --testNamePattern "total > sums"'), ["--config", "test config.ts", "--testNamePattern", "total > sums"])
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            entries = [{"file": str(repo / "tests/total.test.ts"), "name": "total > sums"}]
            self.assertEqual(tiers.collected_ids(repo, entries), ["tests/total.test.ts::total > sums"])
            with self.assertRaises(ValueError):
                tiers.collected_ids(repo, entries * 2)
            with self.assertRaises(ValueError):
                tiers.collected_ids(repo, [{"file": "../foreign.test.ts", "name": "foreign"}])
            with self.assertRaises(ValueError):
                tiers.collected_ids(repo, {})

    def test_missing_runner_and_selector_are_blocking(self):
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            self.assertTrue(tiers.collect_tier(repo, {}, "t1", repo.parent / "evidence"))
            with self.assertRaises(ValueError):
                tiers.command(repo, {})
            with self.assertRaises(ValueError):
                tiers.command(repo, {"guardrails": {"adapter_vitest": {"command": "vitest"}}})

    def test_stryker_normalizes_every_status_and_scopes_ids(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            repo = root / "repo"
            repo.mkdir()
            source, out = root / "stryker.json", root / "normalised.json"
            mutants = [{"id": str(index), "location": {"start": {"line": 2, "column": 1}, "end": {"line": 4, "column": 2}}, "status": status} for index, status in enumerate(mutation.STATUSES)]
            source.write_text(json.dumps({"schemaVersion": "2", "files": {"src/total.ts": {"mutants": mutants}}}))
            self.assertEqual(mutation.write_report(repo, source, out), [])
            parsed = load_report(out)
            self.assertEqual(len(parsed), len(mutation.STATUSES))
            self.assertEqual({item.status for item in parsed}, {"killed", "survived", "no_tests", "timeout", "incomplete"})
            self.assertTrue(all(item.path == "src/total.ts" for item in parsed))
            self.assertTrue(all(item.id.startswith("src/total.ts::") for item in parsed))
            mutants[0]["status"] = "Unexpected"
            source.write_text(json.dumps({"files": {"src/total.ts": {"mutants": mutants}}}))
            self.assertTrue(mutation.write_report(repo, source, out))

    def test_mutation_tool_names_exact_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(vitest.run(Path.cwd(), {}, ["mutation-tool"]), [])
        self.assertIn(f"{mutation.TOOL}@{mutation.VERSION}", out.getvalue())


if __name__ == "__main__":
    unittest.main()
