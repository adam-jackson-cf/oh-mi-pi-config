from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.structure_naming import rename_residue as residue
from guardrails.foundation.source import python
from guardrails.foundation.source.tree import parse

pytestmark = pytest.mark.fast

ENGINE = Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine"

TYPESCRIPT = b"""import { oracleClient } from "./client";
// oracle comment
const WIRE = "oracle";
export function runOracle(): void {}
export class OraclePlugin {
  oracleState = 1;
  takeOracle(): void {}
}
type OracleShape = { oracleField: number };
"""

GO = b"""package verifier

import oracle "example.com/oracle"

// oracle comment
const wire = "oracle"

type OracleClient struct{ oracleField int }

func (c OracleClient) runOracle() { oracle.NewOracle() }

func RunOracle() { oracle.Call() }
"""

SHAPES = """import oracle_a
from x import oracle_b as oracle_c
with open(p) as oracle_d, g() as (oracle_e, [oracle_f, *oracle_g]):
    oracle_h = 1
try:
    pass
except (oracle_i := E) as oracle_j:
    oracle_k = 1
try:
    pass
except* F as oracle_l:
    pass
match v:
    case [oracle_m, *oracle_n] if (oracle_o := 1):
        oracle_p = 1
    case P(k=oracle_q) as oracle_r:
        pass
type OracleAlias[OracleParameter] = list[OracleParameter]
oracle_s, *oracle_t = 1, 2
(oracle_u) = 1
oracle_v: int = 3
oracle_w: int
oracle_x += 1
for (oracle_y, oracle_z) in k:
    pass
else:
    oracle_aa = 1
async def oracle_ab(oracle_ac=(oracle_ad := 1)):
    oracle_ae = 1
self.oracle_af = 1
m[oracle_ag] = 2
oracle_ah = oracle_ai = 3
print(f"{(oracle_aj := 1)}", key=(oracle_ak := 2), oracle_al=3)
values = [oracle_am := 1, [x for x in (oracle_an := y)], lambda: (oracle_ao := 1)]
del oracle_ap
global oracle_aq


@decorator(oracle_ar := 1)
class OracleClass(Base, metaclass=(oracle_as := M)):
    oracle_at = 1

    class OracleInner:
        oracle_au: int

    def oracle_av(self):
        oracle_aw = 1

    while (oracle_ax := 1):
        oracle_ay = 1


if True:

    def oracle_az():
        pass
"""


class AstDefinedNames(ast.NodeVisitor):
    skipped = (
        ast.Import,
        ast.ImportFrom,
        ast.Lambda,
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
    )

    def __init__(self) -> None:
        self.names: list[str] = []

    def visit(self, node: ast.AST) -> None:
        if not isinstance(node, self.skipped):
            super().visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.append(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.append(node.name)
        for statement in node.body:
            self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.names.append(node.id)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.append(node.name)
        for statement in node.body:
            self.visit(statement)


def ast_names(source: str) -> list[str]:
    visitor = AstDefinedNames()
    visitor.visit(ast.parse(source))
    return list(dict.fromkeys(visitor.names))


def leftovers(path: str, source: bytes) -> set[str]:
    renames = [residue.Rename("oracle/api" + Path(path).suffix, path)]
    return {
        finding.message.split("`")[1]
        for finding in residue.residue_findings(renames, {path: source})
    }


class ScriptResidueTests(unittest.TestCase):
    def test_typescript_declared_names_include_members_but_not_imports(self) -> None:
        assert leftovers("verifier/api.ts", TYPESCRIPT) == {
            "runOracle",
            "OraclePlugin",
            "oracleState",
            "takeOracle",
            "OracleShape",
            "oracleField",
        }

    def test_every_script_extension_is_read(self) -> None:
        for suffix in (".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"):
            with self.subTest(suffix=suffix):
                assert leftovers(
                    f"verifier/api{suffix}", b"export function runOracle() {}\n"
                ) == {"runOracle"}

    def test_invalid_typescript_fails_closed(self) -> None:
        findings = residue.residue_findings(
            [residue.Rename("oracle/api.ts", "verifier/api.ts")],
            {"verifier/api.ts": b"function broken(: void {\n"},
        )
        assert len(findings) == 1
        assert "cannot parse verifier/api.ts as typescript" in findings[0].message


class GoResidueTests(unittest.TestCase):
    def test_go_declared_names_include_fields_and_methods_but_not_imports_or_calls(
        self,
    ) -> None:
        assert leftovers("verifier/api.go", GO) == {
            "OracleClient",
            "oracleField",
            "runOracle",
            "RunOracle",
        }

    def test_a_go_package_clause_keeping_the_old_name_is_residue(self) -> None:
        source = b"package oracle\n\nfunc Run() {}\n"
        assert leftovers("verifier/api.go", source) == {"oracle"}

    def test_invalid_go_fails_closed(self) -> None:
        findings = residue.residue_findings(
            [residue.Rename("oracle/api.go", "verifier/api.go")],
            {"verifier/api.go": b"package verifier\n\nfunc (\n"},
        )
        assert len(findings) == 1
        assert "cannot parse verifier/api.go as go" in findings[0].message

    def test_source_that_is_not_utf8_fails_closed(self) -> None:
        findings = residue.residue_findings(
            [residue.Rename("oracle/api.go", "verifier/api.go")],
            {"verifier/api.go": b"package verifier\n\xff\n"},
        )
        assert len(findings) == 1
        assert "cannot read head source" in findings[0].message


class PythonEquivalenceTests(unittest.TestCase):
    def test_every_binding_shape_matches_the_ast_reader(self) -> None:
        names = python.bound_names(parse("m.py", SHAPES))
        assert names == ast_names(SHAPES)
        assert "oracle_ab" in names
        assert "oracle_aj" in names
        assert "oracle_a" not in names
        assert "oracle_ac" not in names

    def test_engine_sources_match_the_ast_reader(self) -> None:
        paths = sorted((ENGINE / "guardrails").rglob("*.py"))
        assert len(paths) > 100
        for path in paths:
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ENGINE).as_posix()):
                assert python.bound_names(parse("m.py", source)) == ast_names(source)

    def test_stub_files_are_read_as_python(self) -> None:
        assert leftovers("verifier/api.pyi", b"def run_oracle() -> None: ...\n") == {
            "run_oracle"
        }


if __name__ == "__main__":
    unittest.main()
