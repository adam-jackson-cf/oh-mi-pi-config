"""Whole-name retirement and scoped, defined-name residue reference fixtures."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.structure import rename_residue as residue

pytestmark = pytest.mark.fast


class RenameResidueTests(unittest.TestCase):
    def test_oracle_to_verifier_reports_all_four_leftovers(self) -> None:
        renames = [
            residue.Rename("oracle/grader.py", "verifier/grader.py"),
            residue.Rename(
                "oracle/runtime/raidar_oracle_plugin.py",
                "verifier/runtime/raidar_oracle_plugin.py",
            ),
        ]
        files = {
            "verifier/grader.py": b"def run_oracle():\n    pass\n",
            "verifier/runtime/raidar_oracle_plugin.py": (
                b"class OraclePlugin:\n"
                b"    def _take_oracle_environment(self):\n"
                b"        pass\n"
            ),
        }
        findings = residue.residue_findings(renames, files)
        assert len(findings) == 4
        expected = {
            ("verifier/grader.py", "run_oracle"),
            ("verifier/runtime/raidar_oracle_plugin.py", "raidar_oracle_plugin"),
            ("verifier/runtime/raidar_oracle_plugin.py", "OraclePlugin"),
            ("verifier/runtime/raidar_oracle_plugin.py", "_take_oracle_environment"),
        }
        assert {
            (item.path, item.message.split("`")[1]) for item in findings
        } == expected
        assert all(item.rule_id == "RENAME.residue" for item in findings)
        assert all("naming.md" in item.message for item in findings)

    def test_gate_effort_retires_the_compound_not_gate(self) -> None:
        renames = [
            residue.Rename(
                "scoring/gate_effort/scorer.py", "scoring/verification_effort/scorer.py"
            )
        ]
        terms = residue.retired_terms(
            renames, ["scoring/verification_effort/scorer.py"]
        )
        assert [(term.old, term.new) for term in terms] == [
            ("gate_effort", "verification_effort")
        ]
        files = {
            "scoring/verification_effort/scorer.py": (
                b'"""verification-effort: what the gate/1 checks cost the agent."""\n'
                b'GATE_KEY = "gate/1"\n'
                b"class GateRunView:\n    pass\n"
                b"class StepGate:\n    pass\n"
            )
        }
        assert residue.residue_findings(renames, files) == []

    def test_compound_leftovers_still_fail(self) -> None:
        renames = [
            residue.Rename("gate_effort/scorer.py", "verification_effort/scorer.py")
        ]
        files = {
            "verification_effort/scorer.py": b"def read_gate_effort():\n    pass\n"
        }
        assert len(residue.residue_findings(renames, files)) == 1

    def test_prefix_homonym_outside_renamed_folder_is_not_residue(self) -> None:
        renames = [
            residue.Rename("grading/prefix/store.py", "grading/pre_fix/store.py")
        ]
        files = {
            "grading/pre_fix/store.py": b"def store():\n    pass\n",
            "grading/sandbox.py": b"def coverage_prefix():\n    pass\n",
            "grading/pre_fixture/other.py": b"def prefix():\n    pass\n",
        }
        assert [term.old for term in residue.retired_terms(renames, list(files))] == [
            "prefix"
        ]
        assert residue.residue_findings(renames, files) == []

    def test_coverage_offline_regroup_retires_nothing(self) -> None:
        renames = [
            residue.Rename("grading/coverage_offline.py", "grading/coverage/offline.py")
        ]
        assert residue.retired_terms(renames, ["grading/coverage/offline.py"]) == []
        assert (
            residue.residue_findings(
                renames,
                {"grading/coverage/offline.py": b"def coverage_offline():\n    pass\n"},
            )
            == []
        )

    def test_imports_strings_comments_docstrings_and_locals_are_ignored(self) -> None:
        renames = [residue.Rename("oracle/api.py", "verifier/api.py")]
        files = {
            "verifier/api.py": (
                b'"""oracle module documentation"""\n'
                b"import oracle\n"
                b"from external import OraclePlugin as oracle_plugin\n"
                b"# oracle comment\n"
                b'WIRE_NAME = "oracle"\n'
                b"def run():\n"
                b'    """oracle function documentation"""\n'
                b"    oracle_local = 1\n"
                b"    def oracle_inner():\n        pass\n"
                b"class Plugin:\n"
                b'    """oracle class documentation"""\n'
                b"    def run(self):\n        oracle_local = 1\n"
            )
        }
        assert residue.residue_findings(renames, files) == []

    def test_module_and_class_bindings_are_definitions(self) -> None:
        renames = [residue.Rename("oracle/api.py", "verifier/api.py")]
        files = {
            "verifier/api.py": (
                b"ORACLE_KEY = 1\n"
                b"class Plugin:\n"
                b"    oracle_state: str\n"
                b"    async def takeOracle(self):\n        pass\n"
            )
        }
        assert len(residue.residue_findings(renames, files)) == 3

    def test_word_matching_is_case_insensitive_contiguous_and_not_substring(
        self,
    ) -> None:
        assert residue.words("HTTPOraclePlugin") == ("http", "oracle", "plugin")
        assert residue.words("gate-effort") == ("gate", "effort")
        assert residue.contains(residue.words("readGateEffort"), ("gate", "effort"))
        assert not residue.contains(
            residue.words("gate_other_effort"), ("gate", "effort")
        )
        assert not residue.contains(residue.words("oracles"), ("oracle",))
        assert not residue.contains(residue.words("pre_fix"), ("prefix",))

    def test_old_named_compound_file_cannot_hide_directory_retirement(self) -> None:
        renames = [residue.Rename("oracle/api.py", "verifier/oracle_api.py")]
        files = {"verifier/oracle_api.py": b""}
        terms = residue.retired_terms(renames, list(files))
        assert ("oracle", "verifier") in [(term.old, term.new) for term in terms]
        findings = residue.residue_findings(renames, files)
        assert any("defines `oracle_api`" in finding.message for finding in findings)

    def test_split_preserves_an_owner_still_present_at_head(self) -> None:
        renames = [
            residue.Rename("process_compliance/checks.py", "process_checks/checks.py")
        ]
        files = {
            "process_checks/checks.py": b"def process_compliance_checks():\n    pass\n",
            "process_compliance/records.py": b"",
        }
        assert residue.retired_terms(renames, list(files)) == []
        assert residue.residue_findings(renames, files) == []

    def test_retained_module_owner_uses_whole_case_insensitive_words(self) -> None:
        renames = [
            residue.Rename("gate_effort/check.py", "verification_effort/check.py")
        ]
        files = {
            "verification_effort/check.py": b"def gate_effort():\n    pass\n",
            "metrics/GateEffort.py": b"",
        }
        assert residue.residue_findings(renames, files) == []

    def test_retained_owner_does_not_match_a_partial_segment(self) -> None:
        renames = [
            residue.Rename("gate_effort/check.py", "verification_effort/check.py")
        ]
        files = {
            "verification_effort/check.py": b"def gate_effort():\n    pass\n",
            "metrics/gate_effort_report.py": b"",
        }
        assert len(residue.residue_findings(renames, files)) == 1

    def test_file_only_rename_does_not_scan_its_siblings(self) -> None:
        renames = [residue.Rename("grading/oracle.py", "grading/verifier.py")]
        files = {
            "grading/verifier.py": b"def run_oracle():\n    pass\n",
            "grading/other.py": b"def oracle():\n    pass\n",
        }
        findings = residue.residue_findings(renames, files)
        assert [finding.path for finding in findings] == ["grading/verifier.py"]

    def test_non_python_paths_count_but_their_contents_do_not(self) -> None:
        renames = [residue.Rename("oracle/api.py", "verifier/api.py")]
        files = {
            "verifier/oracle-config.json": b'{"oracle": "oracle"}',
            "verifier/config.json": b'{"oracle": "oracle"}',
        }
        findings = residue.residue_findings(renames, files)
        assert [finding.path for finding in findings] == ["verifier/oracle-config.json"]

    def test_invalid_python_fails_closed(self) -> None:
        findings = residue.residue_findings(
            [residue.Rename("oracle/api.py", "verifier/api.py")],
            {"verifier/api.py": b"def broken(:\n"},
        )
        assert len(findings) == 1
        assert "cannot parse" in findings[0].message


if __name__ == "__main__":
    unittest.main()
