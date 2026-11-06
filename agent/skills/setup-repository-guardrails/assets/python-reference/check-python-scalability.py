#!/usr/bin/env python3
"""Fail on Python scalability patterns."""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path

from python_quality_scope import discover_maintained_python_files, repo_relative_path

MAX_LOOP_DEPTH = 2
# Per-target analyzer-like source prefixes and approved ownership marker paths.
ANALYZER_PREFIX: tuple[str, ...] = ()
OWNERSHIP_MARKERS: tuple[str, ...] = ()


@dataclass(frozen=True)
class Diagnostic:
    code: str
    relpath: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.relpath}:{self.line}: {self.code} {self.message}"


class ScalabilityVisitor(ast.NodeVisitor):
    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.diagnostics: list[Diagnostic] = []
        self._function_stack: list[str] = []
        self._loop_depth = 0
        self._future_annotations = False

    def visit_Module(self, node: ast.Module) -> None:
        self._future_annotations = any(
            isinstance(statement, ast.ImportFrom)
            and statement.module == "__future__"
            and any(alias.name == "annotations" for alias in statement.names)
            for statement in node.body
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_loop(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_loop(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_loop(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        previous_loop_depth = self._loop_depth
        self._loop_depth = 0
        self.visit(node.body)
        self._loop_depth = previous_loop_depth

    def visit_Call(self, node: ast.Call) -> None:
        if self._is_analyzer_file() and self._is_sync_filesystem_call(node):
            self.diagnostics.append(
                Diagnostic(
                    "PYS251",
                    self.relpath,
                    node.lineno,
                    (
                        "synchronous filesystem I/O in analyzer-like code "
                        f"in {self._current_function()}"
                    ),
                )
            )
        if self._is_analyzer_file() and self._is_direct_subprocess_call(node):
            self.diagnostics.append(
                Diagnostic(
                    "PYS252",
                    self.relpath,
                    node.lineno,
                    (
                        "direct subprocess execution in analyzer-like code "
                        f"in {self._current_function()}"
                    ),
                )
            )
        if self._is_analyzer_file() and self._is_direct_env_access_call(node):
            self.diagnostics.append(
                Diagnostic(
                    "PYS252",
                    self.relpath,
                    node.lineno,
                    (
                        "direct environment read in analyzer-like code "
                        f"in {self._current_function()}"
                    ),
                )
            )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            self._is_analyzer_file()
            and isinstance(node.ctx, ast.Load)
            and self._is_os_environ_subscript(node)
        ):
            self.diagnostics.append(
                Diagnostic(
                    "PYS252",
                    self.relpath,
                    node.lineno,
                    (
                        "direct environment read in analyzer-like code "
                        f"in {self._current_function()}"
                    ),
                )
            )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_env_assignment(node.targets, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_env_assignment([node.target], node.lineno)
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)
        if not self._function_stack and not self._future_annotations:
            self.visit(node.annotation)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_env_assignment([node.target], node.lineno)
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if not self._future_annotations:
            annotated_arguments = (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
                node.args.vararg,
                node.args.kwarg,
            )
            for argument in annotated_arguments:
                if argument is not None and argument.annotation is not None:
                    self.visit(argument.annotation)
            if node.returns is not None:
                self.visit(node.returns)

        self._function_stack.append(node.name)
        previous_loop_depth = self._loop_depth
        self._loop_depth = 0
        for statement in node.body:
            self.visit(statement)
        self._loop_depth = previous_loop_depth
        self._function_stack.pop()

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self.visit(node.iter)
        self._enter_loop(node.lineno)
        if isinstance(node, ast.While):
            self.visit(node.test)
        else:
            self.visit(node.target)
        for statement in node.body:
            self.visit(statement)
        self._loop_depth -= 1
        for statement in node.orelse:
            self.visit(statement)

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        result_nodes: tuple[ast.expr, ...],
        index: int = 0,
    ) -> None:
        if index == len(generators):
            for result_node in result_nodes:
                self.visit(result_node)
            return
        generator = generators[index]
        self.visit(generator.iter)
        self._enter_loop(generator.target.lineno)
        self.visit(generator.target)
        for condition in generator.ifs:
            self.visit(condition)
        self._visit_comprehension(generators, result_nodes, index + 1)
        self._loop_depth -= 1

    def _enter_loop(self, lineno: int) -> None:
        self._loop_depth += 1
        if self._loop_depth > MAX_LOOP_DEPTH:
            self.diagnostics.append(
                Diagnostic(
                    "PYS250",
                    self.relpath,
                    lineno,
                    (
                        f"nested loop depth {self._loop_depth} exceeds "
                        f"{MAX_LOOP_DEPTH} in {self._current_function()}"
                    ),
                )
            )

    def _check_env_assignment(self, targets: list[ast.expr], lineno: int) -> None:
        if not self._is_analyzer_file():
            return
        if any(self._is_os_environ_subscript(target) for target in targets):
            self.diagnostics.append(
                Diagnostic(
                    "PYS252",
                    self.relpath,
                    lineno,
                    (
                        "direct os.environ mutation in analyzer-like code "
                        f"in {self._current_function()}"
                    ),
                )
            )

    def _current_function(self) -> str:
        return self._function_stack[-1] if self._function_stack else "<module>"

    def _is_analyzer_file(self) -> bool:
        return self.relpath.startswith(ANALYZER_PREFIX)

    @staticmethod
    def _is_direct_subprocess_call(node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"run", "Popen"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        )

    @staticmethod
    def _is_direct_env_access_call(node: ast.Call) -> bool:
        if not isinstance(node.func, ast.Attribute):
            return False
        if (
            node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        ):
            return True
        return (
            node.func.attr in {"copy", "get"}
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "os"
        )

    @staticmethod
    def _is_sync_filesystem_call(node: ast.Call) -> bool:
        if isinstance(node.func, ast.Name):
            return node.func.id == "open"
        if not isinstance(node.func, ast.Attribute):
            return False
        return node.func.attr in {
            "read_bytes",
            "read_text",
            "write_bytes",
            "write_text",
        }

    @staticmethod
    def _is_os_environ_subscript(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
        )


def diagnostics_for_file(repo_root: Path, path: Path) -> list[Diagnostic]:
    relpath = repo_relative_path(repo_root, path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relpath)
    except SyntaxError as exc:
        line = exc.lineno or 1
        return [
            Diagnostic(
                "PYS000", relpath, line, f"could not parse Python file: {exc.msg}"
            )
        ]
    visitor = ScalabilityVisitor(relpath)
    visitor.visit(tree)
    return visitor.diagnostics


def ownership_diagnostics(repo_root: Path) -> list[Diagnostic]:
    if not ANALYZER_PREFIX:
        return []
    if any((repo_root / marker).is_file() for marker in OWNERSHIP_MARKERS):
        return []
    return [
        Diagnostic(
            "PYS253",
            ", ".join(ANALYZER_PREFIX),
            1,
            "analyzer-like subsystem ownership marker missing",
        )
    ]


def collect_diagnostics(repo_root: Path) -> list[Diagnostic]:
    diagnostics = ownership_diagnostics(repo_root)
    for path in discover_maintained_python_files(repo_root):
        diagnostics.extend(diagnostics_for_file(repo_root, path))
    return sorted(diagnostics, key=lambda item: (item.relpath, item.line, item.code))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root to inspect.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = args.repo_root.resolve()
    diagnostics = collect_diagnostics(repo_root)
    if diagnostics:
        print("Python scalability quality check failed:")
        for diagnostic in diagnostics:
            print(diagnostic.render())
        return 1
    print("Python scalability quality check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
