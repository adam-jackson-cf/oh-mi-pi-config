"""Whole-name retirement and scoped, defined-name residue reference fixtures."""

from __future__ import annotations

import json
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/skills/setup-repo-proofs/assets/python-reference"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails.structure import rename_residue as residue  # noqa: E402


class RenameResidueTests(unittest.TestCase):
    def test_oracle_to_verifier_reports_all_four_leftovers(self) -> None:
        renames = [
            residue.Rename("oracle/grader.py", "verifier/grader.py"),
            residue.Rename("oracle/runtime/raidar_oracle_plugin.py", "verifier/runtime/raidar_oracle_plugin.py"),
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
        self.assertEqual(len(findings), 4)
        expected = {
            ("verifier/grader.py", "run_oracle"),
            ("verifier/runtime/raidar_oracle_plugin.py", "raidar_oracle_plugin"),
            ("verifier/runtime/raidar_oracle_plugin.py", "OraclePlugin"),
            ("verifier/runtime/raidar_oracle_plugin.py", "_take_oracle_environment"),
        }
        self.assertEqual(
            {(item.path, item.message.split("`")[1]) for item in findings}, expected
        )
        self.assertTrue(all(item.rule_id == "RENAME.residue" for item in findings))
        self.assertTrue(all("naming.md" in item.message for item in findings))

    def test_gate_effort_retires_the_compound_not_gate(self) -> None:
        renames = [residue.Rename("scoring/gate_effort/scorer.py", "scoring/verification_effort/scorer.py")]
        terms = residue.retired_terms(renames, ["scoring/verification_effort/scorer.py"])
        self.assertEqual([(term.old, term.new) for term in terms], [("gate_effort", "verification_effort")])
        files = {"scoring/verification_effort/scorer.py": (
            b'"""verification-effort: what the gate/1 checks cost the agent."""\n'
            b'GATE_KEY = "gate/1"\n'
            b"class GateRunView:\n    pass\n"
            b"class StepGate:\n    pass\n"
        )}
        self.assertEqual(residue.residue_findings(renames, files), [])

    def test_compound_leftovers_still_fail(self) -> None:
        renames = [residue.Rename("gate_effort/scorer.py", "verification_effort/scorer.py")]
        files = {"verification_effort/scorer.py": b"def read_gate_effort():\n    pass\n"}
        self.assertEqual(len(residue.residue_findings(renames, files)), 1)

    def test_prefix_homonym_outside_renamed_folder_is_not_residue(self) -> None:
        renames = [residue.Rename("grading/prefix/store.py", "grading/pre_fix/store.py")]
        files = {
            "grading/pre_fix/store.py": b"def store():\n    pass\n",
            "grading/sandbox.py": b"def coverage_prefix():\n    pass\n",
            "grading/pre_fixture/other.py": b"def prefix():\n    pass\n",
        }
        self.assertEqual([term.old for term in residue.retired_terms(renames, list(files))], ["prefix"])
        self.assertEqual(residue.residue_findings(renames, files), [])

    def test_coverage_offline_regroup_retires_nothing(self) -> None:
        renames = [residue.Rename("grading/coverage_offline.py", "grading/coverage/offline.py")]
        self.assertEqual(residue.retired_terms(renames, ["grading/coverage/offline.py"]), [])
        self.assertEqual(residue.residue_findings(renames, {
            "grading/coverage/offline.py": b"def coverage_offline():\n    pass\n"
        }), [])

    def test_imports_strings_comments_docstrings_and_locals_are_ignored(self) -> None:
        renames = [residue.Rename("oracle/api.py", "verifier/api.py")]
        files = {"verifier/api.py": (
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
        )}
        self.assertEqual(residue.residue_findings(renames, files), [])

    def test_module_and_class_bindings_are_definitions(self) -> None:
        renames = [residue.Rename("oracle/api.py", "verifier/api.py")]
        files = {"verifier/api.py": (
            b"ORACLE_KEY = 1\n"
            b"class Plugin:\n"
            b"    oracle_state: str\n"
            b"    async def takeOracle(self):\n        pass\n"
        )}
        self.assertEqual(len(residue.residue_findings(renames, files)), 3)

    def test_word_matching_is_case_insensitive_contiguous_and_not_substring(self) -> None:
        self.assertEqual(residue.words("HTTPOraclePlugin"), ("http", "oracle", "plugin"))
        self.assertEqual(residue.words("gate-effort"), ("gate", "effort"))
        self.assertTrue(residue.contains(residue.words("readGateEffort"), ("gate", "effort")))
        self.assertFalse(residue.contains(residue.words("gate_other_effort"), ("gate", "effort")))
        self.assertFalse(residue.contains(residue.words("oracles"), ("oracle",)))
        self.assertFalse(residue.contains(residue.words("pre_fix"), ("prefix",)))

    def test_old_named_compound_file_cannot_hide_directory_retirement(self) -> None:
        renames = [residue.Rename("oracle/api.py", "verifier/oracle_api.py")]
        files = {"verifier/oracle_api.py": b""}
        terms = residue.retired_terms(renames, list(files))
        self.assertIn(("oracle", "verifier"), [(term.old, term.new) for term in terms])
        findings = residue.residue_findings(renames, files)
        self.assertTrue(any("defines `oracle_api`" in finding.message for finding in findings))

    def test_split_preserves_an_owner_still_present_at_head(self) -> None:
        renames = [
            residue.Rename(
                "process_compliance/checks.py", "process_checks/checks.py"
            )
        ]
        files = {
            "process_checks/checks.py": b"def process_compliance_checks():\n    pass\n",
            "process_compliance/records.py": b"",
        }
        self.assertEqual(residue.retired_terms(renames, list(files)), [])
        self.assertEqual(residue.residue_findings(renames, files), [])

    def test_retained_module_owner_uses_whole_case_insensitive_words(self) -> None:
        renames = [residue.Rename("gate_effort/check.py", "verification_effort/check.py")]
        files = {
            "verification_effort/check.py": b"def gate_effort():\n    pass\n",
            "metrics/GateEffort.py": b"",
        }
        self.assertEqual(residue.residue_findings(renames, files), [])

    def test_retained_owner_does_not_match_a_partial_segment(self) -> None:
        renames = [residue.Rename("gate_effort/check.py", "verification_effort/check.py")]
        files = {
            "verification_effort/check.py": b"def gate_effort():\n    pass\n",
            "metrics/gate_effort_report.py": b"",
        }
        self.assertEqual(len(residue.residue_findings(renames, files)), 1)

    def test_file_only_rename_does_not_scan_its_siblings(self) -> None:
        renames = [residue.Rename("grading/oracle.py", "grading/verifier.py")]
        files = {
            "grading/verifier.py": b"def run_oracle():\n    pass\n",
            "grading/other.py": b"def oracle():\n    pass\n",
        }
        findings = residue.residue_findings(renames, files)
        self.assertEqual([finding.path for finding in findings], ["grading/verifier.py"])

    def test_non_python_paths_count_but_their_contents_do_not(self) -> None:
        renames = [residue.Rename("oracle/api.py", "verifier/api.py")]
        files = {
            "verifier/oracle-config.json": b'{"oracle": "oracle"}',
            "verifier/config.json": b'{"oracle": "oracle"}',
        }
        findings = residue.residue_findings(renames, files)
        self.assertEqual([finding.path for finding in findings], ["verifier/oracle-config.json"])

    def test_invalid_python_fails_closed(self) -> None:
        findings = residue.residue_findings(
            [residue.Rename("oracle/api.py", "verifier/api.py")],
            {"verifier/api.py": b"def broken(:\n"},
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("cannot parse", findings[0].message)



class RenameResidueCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-rename-residue-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        self.record = Path(scratch.name) / "record"
        self.repo.mkdir()
        self.record.mkdir()
        self.profile: dict = {}
        self.write_profile()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()

    def write_profile(self) -> None:
        (self.record / "profile.md").write_text("---\n" + json.dumps(self.profile) + "\n---\n")

    def write(self, path: str, text: str) -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, subject: str) -> str:
        self.git("add", ".")
        self.git("commit", "-qm", subject, "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def move(self, old: str, new: str) -> None:
        target = self.repo / new
        target.parent.mkdir(parents=True, exist_ok=True)
        (self.repo / old).rename(target)

    def command(self, base: str, head: str = "HEAD") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, "-I", "-S", "-c",
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "from omp_guardrails.cli import main; raise SystemExit(main())",
                str(REFERENCE),
                "rename-residue", "--repo", str(self.repo), "--record", str(self.record),
                "--base", base, "--head", head,
            ],
            cwd=REFERENCE, capture_output=True, text=True, check=False, timeout=20,
        )

    def assert_clean(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("RENAME.residue:", result.stdout)

    def test_oracle_to_verifier_reports_exactly_four_findings(self) -> None:
        self.write("oracle/grader.py", "def run_oracle():\n    return 'grader result'\n")
        self.write(
            "oracle/runtime/raidar_oracle_plugin.py",
            "class OraclePlugin:\n"
            "    def _take_oracle_environment(self):\n"
            "        return 'plugin environment'\n",
        )
        base = self.commit("Oracle owner")
        self.move("oracle/grader.py", "verifier/grader.py")
        self.move("oracle/runtime/raidar_oracle_plugin.py", "verifier/runtime/raidar_oracle_plugin.py")
        self.commit("Rename oracle owner to verifier")
        result = self.command(base)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        lines = [line for line in result.stdout.splitlines() if "RENAME.residue:" in line]
        self.assertEqual(len(lines), 4, result.stdout)
        for path, identifier in [
            ("verifier/grader.py", "run_oracle"),
            ("verifier/runtime/raidar_oracle_plugin.py", "raidar_oracle_plugin"),
            ("verifier/runtime/raidar_oracle_plugin.py", "OraclePlugin"),
            ("verifier/runtime/raidar_oracle_plugin.py", "_take_oracle_environment"),
        ]:
            self.assertTrue(any(
                line.startswith(path + ":") and f"defines `{identifier}`" in line
                for line in lines
            ), result.stdout)

    def test_gate_effort_rename_keeps_gate_mechanism_valid(self) -> None:
        self.write(
            "scoring/gate_effort/scorer.py",
            '"""verification-effort: what the gate/1 checks cost the agent."""\n'
            'GATE_KEY = "gate/1"\n'
            "class GateRunView:\n    pass\n"
            "class StepGate:\n    pass\n",
        )
        base = self.commit("Gate effort metric")
        self.move("scoring/gate_effort/scorer.py", "scoring/verification_effort/scorer.py")
        self.commit("Rename metric while preserving gate mechanism")
        self.assert_clean(self.command(base))

    def test_prefix_homonym_outside_renamed_folder_is_valid(self) -> None:
        self.write("grading/prefix/store.py", "def store():\n    return 'prefix record'\n")
        self.write("grading/sandbox.py", "def coverage_prefix():\n    return 'sandbox'\n")
        base = self.commit("Prefix owner and unrelated homonym")
        self.move("grading/prefix/store.py", "grading/pre_fix/store.py")
        self.commit("Rename prefix owner only")
        self.assert_clean(self.command(base))

    def test_coverage_offline_regroup_is_valid(self) -> None:
        self.write("grading/coverage_offline.py", "def coverage_offline():\n    return 'coverage'\n")
        base = self.commit("Coverage offline module")
        self.move("grading/coverage_offline.py", "grading/coverage/offline.py")
        self.commit("Group coverage without retiring its words")
        self.assert_clean(self.command(base))

    def test_split_with_remaining_process_compliance_owner_is_valid(self) -> None:
        self.write("process_compliance/checks.py", "def process_compliance_checks():\n    return 'checks'\n")
        self.write("process_compliance/records.py", "CONTRACT = 'compliance records'\n")
        base = self.commit("Compliance owns checks and records")
        self.move("process_compliance/checks.py", "process_checks/checks.py")
        self.commit("Split checks from remaining compliance owner")
        self.assert_clean(self.command(base))

    def test_head_snapshot_ignores_dirty_worktree(self) -> None:
        self.write("grading/oracle.py", "def run_oracle():\n    return 'renamed module'\n")
        base = self.commit("Existing module")
        self.move("grading/oracle.py", "grading/verifier.py")
        head = self.commit("Rename one module")
        self.write("grading/verifier.py", "invalid uncommitted Python !!!")
        result = self.command(base, head)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("defines `run_oracle`", result.stdout)
        self.assertNotIn("cannot parse", result.stdout)

    def test_documented_non_owned_paths_are_excluded(self) -> None:
        self.profile = {"guardrails": {"check": {"exclude": ["verifier/vendor"]}}}
        self.write_profile()
        self.write("oracle/api.py", "def run():\n    return 'owned API'\n")
        self.write("oracle/vendor/plugin.py", "class OraclePlugin:\n    pass\n")
        base = self.commit("Owned API and vendor plugin")
        self.move("oracle/api.py", "verifier/api.py")
        self.move("oracle/vendor/plugin.py", "verifier/vendor/plugin.py")
        self.commit("Move owner including unowned vendor")
        self.assert_clean(self.command(base))

    def test_pinned_rename_detection_overrides_disabled_repo_setting(self) -> None:
        self.write("oracle/api.py", "def run_oracle():\n    return 'unique oracle source'\n")
        base = self.commit("Existing owner")
        self.move("oracle/api.py", "verifier/api.py")
        self.commit("Rename owner")
        self.git("config", "diff.renames", "false")
        self.git("config", "diff.renameLimit", "1")
        result = self.command(base)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("defines `run_oracle`", result.stdout)


if __name__ == "__main__":
    unittest.main()
