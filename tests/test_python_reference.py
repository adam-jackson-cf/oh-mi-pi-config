"""Exercise reference AST checks with independent target configurations."""

from __future__ import annotations

import ast
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "agent/skills/setup-repository-guardrails/assets/python-reference"
)


class PythonReferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.modules = patch.dict(sys.modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.scope = self._load("python_quality_scope", "python_quality_scope.py")
        self.quality = self._load("reference_quality", "check-python-quality.py")
        self.scalability = self._load("reference_scalability", "check-python-scalability.py")

    @staticmethod
    def _load(name: str, filename: str):
        spec = importlib.util.spec_from_file_location(name, REFERENCE / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_maintained_roots_and_nested_test_groups_are_target_owned(self) -> None:
        for root in ("src/", "components/runtime/"):
            with self.subTest(root=root):
                self.scope.MAINTAINED_ROOTS = (root,)
                self.quality.MAINTAINED_ROOTS = (root,)
                self.quality.TEST_ROOTS = (f"{root}checks/",)
                self.quality.ALLOWED_TEST_GROUPS = {"fast"}
                path = f"{root}checks/fast/test_invoice.py"
                assert self.scope.is_maintained_python_relpath(path)
                assert not self.quality.structure_diagnostics_for_relpath(path)
                codes = {
                    item.code for item in self.quality.structure_diagnostics_for_relpath(
                        f"{root}checks/slow/test_invoice.py"
                    )
                }
                assert "PYQ102" in codes
                assert not self.scope.is_maintained_python_relpath("outside/file.py")
        self.quality.MAINTAINED_ROOTS = ()
        assert self.quality.structure_diagnostics_for_relpath("src/file.py")[0].code == "PYQ100"

    def test_analyzer_bases_and_entrypoints_use_target_names(self) -> None:
        self.quality.ANALYZER_ROOTS = ("src/inspection/",)
        self.quality.APPROVED_ANALYZER_BASES = {"InspectionBase"}
        self.quality.PROJECT_ANALYZER_ENTRYPOINTS = {"InspectionBase": "inspect_project"}
        path = "src/inspection/invoice.py"
        for source, expected in (
            ("class InvoiceAnalyzer: pass", {"PYQ210"}),
            ("class InvoiceAnalyzer(InspectionBase): pass", {"PYQ211"}),
            ("class InvoiceAnalyzer(InspectionBase):\n def inspect_project(self): pass", set()),
        ):
            with self.subTest(source=source):
                assert {
                    item.code for item in self.quality.class_interface_diagnostics_for_source(source, path)
                } == expected

    def test_seam_and_contract_checks_use_target_paths(self) -> None:
        path = "src/adapters/process.py"
        source = "def execute():\n subprocess.run(['tool'])\n"
        check = self.quality.class_interface_diagnostics_for_source
        assert {item.code for item in check(source, path)} == {"PYQ220"}
        self.quality.SEAM_SUBPROCESS_WRAPPERS = {path: {"execute"}}
        assert not check(source, path)
        self.quality.DYNAMIC_IMPORT_ROOTS = ("src/adapters/",)
        assert {item.code for item in check("importlib.import_module('plugin')", path)} == {"PYQ221"}
        self.quality.ADAPTER_IMPORT_ALLOWLIST = {path}
        assert not check("importlib.import_module('plugin')", path)
        self.quality.CONTRACT_FILES = {path}
        assert {item.code for item in check("callback: Callable\nservice: Any", path)} == {"PYQ230", "PYQ231"}
        assert not check("callback: Callback\nservice: Service", path)

    def test_scalability_scope_and_ownership_are_target_owned(self) -> None:
        self.scalability.ANALYZER_PREFIX = ("lib/inspection/",)
        self.scalability.OWNERSHIP_MARKERS = ("lib/inspection/OWNERS",)
        for path, expected in (("lib/inspection/a.py", {"PYS251", "PYS252"}), ("lib/io.py", set())):
            with self.subTest(path=path):
                visitor = self.scalability.ScalabilityVisitor(path)
                visitor.visit(ast.parse("open('file')\nsubprocess.run(['tool'])"))
                assert {item.code for item in visitor.diagnostics} == expected
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assert self.scalability.ownership_diagnostics(root)[0].code == "PYS253"
            marker = root / "lib/inspection/OWNERS"
            marker.parent.mkdir(parents=True)
            marker.write_text("inspection team\n")
            assert not self.scalability.ownership_diagnostics(root)
