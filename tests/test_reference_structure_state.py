"""Git-object fixture outcomes for bounded structure judgment evidence."""

from __future__ import annotations

import contextlib
import io
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

from guardrails.commands import cli
from guardrails.commands.structure import state

pytestmark = pytest.mark.seam

ROOT = Path(__file__).resolve().parents[1]


class StructureStateTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-structure-state-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.profile = {
            "structure": {
                "import_direction": {"source_root": "src/shop", "package": "shop"}
            }
        }

    def git(self, *args: str) -> str:
        return run_text(
            ["git", "-C", str(self.repo), *args], check=True, timeout=10
        ).stdout.strip()

    def write(self, path: str, text: str = "") -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def write_many(self, folder: str, count: int) -> None:
        target = self.repo / folder
        target.mkdir(parents=True, exist_ok=True)
        for index in range(count):
            (target / f"part_{index:05}.json").write_text("{}")

    def unit(
        self, base: str, head: str, folder: str, candidate_base: str | None = None
    ) -> dict[str, Any]:
        argv = ["--base", base, "--head", head, "--unit", folder]
        if candidate_base is not None:
            argv += ["--candidate-base", candidate_base]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert state.run(self.repo, self.profile, argv) == []
        units: dict[str, dict[str, Any]] = json.loads(output.getvalue())
        return units[folder]

    def commit(self, subject: str) -> str:
        self.git("add", ".")
        self.git("commit", "-qm", subject, "--allow-empty")
        return self.git("rev-parse", "HEAD")

    def test_new_folder_exposes_relationships_and_head_semantics(self) -> None:
        self.write("src/shop/primitive/gate.py", "def gate(): pass\n")
        self.write(
            "src/shop/scoring/other/reader.py",
            "from shop.scoring.verification_effort.records import GATE_KEY\n",
        )
        base = self.commit("Existing mechanism and peer")
        self.write(
            "src/shop/scoring/verification_effort/records.py",
            'GATE_KEY = "gate/1"\nimport third_party\n',
        )
        self.write(
            "src/shop/scoring/verification_effort/scorer.py",
            '''"""Verification effort: what gate/1 costs.

Ignored second paragraph.
"""
from .records import GATE_KEY
from third_party import imported_name

def gate_cost():
    """Read the mechanism record.

    More explanation.
    """
    return GATE_KEY
''',
        )
        head = self.commit("Add metric folder")
        result = state.extract(self.repo, base, head, self.profile)
        unit = result["src/shop/scoring/verification_effort"]
        scorer = "src/shop/scoring/verification_effort/scorer.py"
        records = "src/shop/scoring/verification_effort/records.py"
        assert list(result) == ["src/shop/scoring/verification_effort"]
        assert unit["top_level_symbols"][scorer] == ["gate_cost"]
        assert unit["constant_values"][records] == {"GATE_KEY": "gate/1"}
        assert unit["docstrings"][scorer] == {
            "<module>": "Verification effort: what gate/1 costs.",
            "gate_cost": "Read the mechanism record.",
        }
        assert unit["term_owners"]["gate"] == ["src/shop/primitive/gate.py"]
        assert unit["import_edges"]["internal"] == [
            {
                "from": scorer,
                "module": "shop.scoring.verification_effort.records.GATE_KEY",
                "to": [records],
            }
        ]
        assert (
            unit["import_edges"]["inbound"][0]["from"]
            == "src/shop/scoring/other/reader.py"
        )
        assert any(not row["to"] for row in unit["import_edges"]["outbound"])
        assert unit["sibling_context"][0]["files"] == [
            "src/shop/scoring/other/reader.py"
        ]
        assert [row["unit_path"] for row in unit["sibling_context"]] == [
            "src/shop/scoring/other",
            "src/shop/scoring/verification_effort",
        ]
        assert unit["file_facts"][scorer] == {
            "bytes": len(Path(self.repo, scorer).read_bytes()),
            "lines": len(Path(self.repo, scorer).read_bytes().splitlines()),
            "functions": 1,
            "classes": 0,
        }
        assert unit["evidence_limits"]["truncations"] == []
        self.write(scorer, "invalid python !!!")
        self.git("add", scorer)
        self.write("src/shop/scoring/verification_effort/untracked.py", "")
        assert state.extract(self.repo, base, head, self.profile) == result

    def test_rename_residue_is_scoped_and_whole_compounds_retire(self) -> None:
        source = '"""A verification metric."""\nGATE_KEY = "gate/1"\ndef gate_cost(): return GATE_KEY\n'
        self.write("src/shop/scoring/gate_effort/scorer.py", source)
        base = self.commit("Old metric")
        old = self.repo / "src/shop/scoring/gate_effort/scorer.py"
        self.write("src/shop/scoring/verification_effort/scorer.py", source)
        old.unlink()
        head = self.commit("Rename whole metric")
        unit = state.extract(self.repo, base, head, self.profile)[
            "src/shop/scoring/verification_effort"
        ]
        assert [row["old"] for row in unit["change"]["retired_terms"]] == [
            "gate_effort"
        ]
        assert unit["change"]["residue"] == []
        assert (
            unit["change"]["renames"][0]["old"]
            == "src/shop/scoring/gate_effort/scorer.py"
        )
        assert (
            unit["related_context"][0]["folder_path"] == "src/shop/scoring/gate_effort"
        )

    def test_retired_simple_name_residue_reuses_precheck(self) -> None:
        source = "def run_oracle():\n    return 'a unique verifier payload with enough unchanged bytes'\n"
        self.write("src/shop/oracle/runner.py", source)
        self.write("src/shop/outside.py", "def coverage_oracle(): pass\n")
        base = self.commit("Oracle owner")
        self.write("src/shop/verifier/runner.py", source)
        (self.repo / "src/shop/oracle/runner.py").unlink()
        head = self.commit("Move owner")
        unit = state.extract(self.repo, base, head, self.profile)["src/shop/verifier"]
        assert len(unit["change"]["residue"]) == 1
        assert "run_oracle" in unit["change"]["residue"][0]["message"]

    def test_regrouping_does_not_retire_a_compound(self) -> None:
        source = "def produce(): return 'unique coverage producer payload'\n"
        self.write("src/shop/coverage_offline.py", source)
        base = self.commit("Flat file")
        self.write("src/shop/coverage/offline.py", source)
        (self.repo / "src/shop/coverage_offline.py").unlink()
        head = self.commit("Group capability")
        unit = state.extract(self.repo, base, head, self.profile)["src/shop/coverage"]
        assert unit["change"]["retired_terms"] == []

    def test_historical_explicit_unit_uses_no_future_change(self) -> None:
        empty = self.commit("Empty tree")
        self.write("src/shop/coverage_store.py", "RECORD = 'coverage'\n")
        self.write(
            "src/shop/coverage_offline.py", "from .coverage_store import RECORD\n"
        )
        head = self.commit("Introduce producer and store")
        empty_tree = self.git("rev-parse", empty + "^{tree}")
        unit = self.unit(head, head, "src/shop", empty_tree)
        assert unit["change"] == {"renames": [], "retired_terms": [], "residue": []}
        (cluster,) = unit["cluster"]
        assert cluster["prefix"] == "coverage"
        assert cluster["co_change"] == [
            {
                "members": [
                    "src/shop/coverage_offline.py",
                    "src/shop/coverage_store.py",
                ],
                "count": 1,
            }
        ]
        assert len(cluster["internal_edges"]) == 1
        assert cluster["eligible"]
        assert self.unit(head, head, "src/shop")["cluster"] == []
        assert state.extract(self.repo, head, head) == {}

    def test_limits_record_omissions_and_parse_failures(self) -> None:
        base = self.commit("Empty")
        docstring = "Long explanatory module docstring " * 20
        self.write("src/shop/a.py", f'"""{docstring}"""\nA = "a"\nB = "b"\n')
        self.write("src/shop/b.py", "not valid python !!!")
        self.write("src/shop/c.py", "x = 1\n" * 50000)
        head = self.commit("Evidence requiring limits")
        unit = state.extract(self.repo, base, head)["src/shop"]
        fields = {row["field"] for row in unit["evidence_limits"]["truncations"]}
        assert "docstrings" in fields
        assert "python_source" in fields
        assert unit["evidence_limits"]["parse_errors"][0]["path"] == "src/shop/b.py"
        assert (
            unit["docstrings"]["src/shop/a.py"]["<module>"]
            == docstring[: state.CAPS["docstring_chars"]]
        )
        assert "src/shop/c.py" not in unit["top_level_symbols"]
        assert "src/shop/c.py" in unit["files"]

    def test_tree_unit_and_history_caps_are_declared(self) -> None:
        base = self.commit("Empty")
        self.write_many("src/shop", state.CAPS["unit_files"] + 1)
        head = self.commit("More files than one unit holds")
        unit = self.unit(base, head, "src/shop")
        assert len(unit["files"]) == state.CAPS["unit_files"]
        assert any(
            row["field"] == "src/shop:files"
            for row in unit["evidence_limits"]["truncations"]
        )
        self.write_many("src/bulk", state.CAPS["tree_files"])
        head = self.commit("More files than the tree evidence holds")
        unit = self.unit(base, head, "src/shop")
        assert any(
            row["field"] == "tree" for row in unit["evidence_limits"]["truncations"]
        )

    def test_exclusions_and_non_python_files(self) -> None:
        base = self.commit("Empty")
        self.write("vendor/pair_one.py")
        self.write("vendor/pair_two.py")
        self.write("docs/contract.json", '{"name":"contract"}')
        head = self.commit("Owned document and vendor")
        result = state.extract(
            self.repo, base, head, {"guardrails": {"check": {"exclude": ["vendor"]}}}
        )
        assert list(result) == ["docs"]
        assert result["docs"]["top_level_symbols"] == {}

    def test_cli_contract_needs_no_record_and_bad_revision_is_not_empty_success(
        self,
    ) -> None:
        base = self.commit("Empty")
        self.write("src/shop/owner.py", "VALUE = 1\n")
        head = self.commit("Owner")
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            status = cli.main([
                "structure-state",
                "--repo",
                str(self.repo),
                "--base",
                base,
                "--head",
                head,
            ])
        assert status == 0
        assert list(json.loads(output.getvalue())) == ["src", "src/shop"]
        assert "structure-state: ok" in errors.getvalue()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            findings = state.run(
                self.repo, {}, ["--base", "missing-ref", "--head", head]
            )
        assert output.getvalue() == ""
        assert [f.rule_id for f in findings] == ["STRUCTURE.state"]

    def test_changed_files_keep_their_evidence_beyond_tree_evidence_cap(self) -> None:
        self.write_many("bulk", state.CAPS["tree_files"])
        base = self.commit("A tree at the evidence cap")
        folders = [f"src/owner_{index:03}" for index in range(70)]
        for folder in folders:
            self.write(folder + "/contract.json", "{}")
        self.write("zz/late.py", "def late_symbol():\n    pass\n")
        head = self.commit("Seventy destination owners and a late module")
        result = state.extract(self.repo, base, head)
        assert list(result) == ["src", *folders, "zz"]
        assert result[folders[-1]]["files"] == [folders[-1] + "/contract.json"]
        assert result["zz"]["top_level_symbols"] == {"zz/late.py": ["late_symbol"]}
        assert any(
            row["field"] == "tree"
            for row in result["zz"]["evidence_limits"]["truncations"]
        )

    def test_compact_state_byte_budget_preserves_schema_and_reports_lost_evidence(
        self,
    ) -> None:
        base = self.commit("Empty")
        docs = "".join(
            f'def capability_{index}():\n    """'
            + "explanation " * 40
            + '"""\n    pass\n'
            for index in range(128)
        )
        self.write("src/shop/owner.py", docs)
        head = self.commit("Large semantic evidence")
        unit = state.extract(self.repo, base, head)["src/shop"]
        assert len(state.compact_json(unit).encode()) <= state.CAPS["state_bytes"]
        assert unit["evidence_limits"]["state_budget_omissions"]
        assert set(unit["import_edges"]) == {"inbound", "outbound", "internal"}
        assert set(unit["change"]) == {"renames", "retired_terms", "residue"}

    def test_top_level_bindings_exclude_imports_and_class_or_function_locals(
        self,
    ) -> None:
        base = self.commit("Empty")
        self.write(
            "src/shop/owner.py",
            """from external import Imported
if True:
    CONDITIONAL = 1
class Owner:
    CLASS_LOCAL = 1
    def method(self): pass
def capability():
    function_local = 1
""",
        )
        head = self.commit("Scope bindings")
        unit = state.extract(self.repo, base, head)["src/shop"]
        assert unit["top_level_symbols"]["src/shop/owner.py"] == [
            "CONDITIONAL",
            "Owner",
            "capability",
        ]


if __name__ == "__main__":
    unittest.main()
