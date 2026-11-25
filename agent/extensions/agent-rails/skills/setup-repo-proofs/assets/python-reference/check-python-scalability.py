#!/usr/bin/env python3
"""Fail on Python scalability patterns."""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from python_quality_scope import discover_maintained_python_files, repo_relative_path

MAX_LOOP_DEPTH = 2
# Per-target analyzer-like source prefixes and approved ownership marker paths.
ANALYZER_PREFIX: tuple[str, ...] = ()
OWNERSHIP_MARKERS: tuple[str, ...] = ()
SYNC_FS_METHODS = frozenset({
    "chmod", "exists", "glob", "is_dir", "is_file", "is_symlink", "iterdir", "lstat", "mkdir",
    "open", "read_bytes", "read_text", "readlink", "rename", "replace", "rglob", "rmdir",
    "samefile", "stat", "symlink_to", "touch", "unlink", "walk", "write_bytes", "write_text",
})
SYNC_FS_MODULE_CALLS: dict[str, frozenset[str]] = {
    "os": frozenset({
        "access", "chmod", "listdir", "lstat", "makedirs", "mkdir", "open", "readlink", "remove",
        "removedirs", "rename", "replace", "rmdir", "scandir", "stat", "symlink", "unlink", "walk",
    }),
    "os.path": frozenset({
        "exists", "getatime", "getctime", "getmtime", "getsize", "isdir", "isfile", "islink",
        "lexists", "realpath", "samefile",
    }),
    "shutil": frozenset({"copy", "copy2", "copyfile", "copytree", "move", "rmtree"}),
    "glob": frozenset({"glob", "iglob"}),
    "io": frozenset({"open"}),
}


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
        self._path_names: frozenset[str] = frozenset()

    def visit_Module(self, node: ast.Module) -> None:
        self._future_annotations = any(
            isinstance(statement, ast.ImportFrom)
            and statement.module == "__future__"
            and any(alias.name == "annotations" for alias in statement.names)
            for statement in node.body
        )
        self._path_names = path_typed_names(node)
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

    def _is_sync_filesystem_call(self, node: ast.Call) -> bool:
        func = node.func
        if isinstance(func, ast.Name):
            return func.id == "open"
        if not isinstance(func, ast.Attribute):
            return False
        owner = ScalabilityVisitor._dotted_name(func.value)
        if owner in SYNC_FS_MODULE_CALLS:
            return func.attr in SYNC_FS_MODULE_CALLS[owner]
        return func.attr in SYNC_FS_METHODS and is_path_expr(func.value, self._path_names)

    @staticmethod
    def _dotted_name(node: ast.expr) -> str | None:
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        parts.append(node.id)
        return ".".join(reversed(parts))

    @staticmethod
    def _is_os_environ_subscript(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "os"
        )


PATH_TYPES = frozenset({"Path", "PurePath", "PosixPath", "WindowsPath"})
PATH_RETURNING = frozenset({"absolute", "expanduser", "joinpath", "resolve", "with_name",
                            "with_stem", "with_suffix"})


def _is_path_annotation(node: ast.expr | None) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split(".")[-1] in PATH_TYPES
    if isinstance(node, ast.Name):
        return node.id in PATH_TYPES
    return isinstance(node, ast.Attribute) and node.attr in PATH_TYPES


def is_path_expr(node: ast.expr, names: frozenset[str]) -> bool:
    """Whether an expression is statically known to be a pathlib path."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return is_path_expr(node.left, names)
    if isinstance(node, ast.Attribute) and node.attr == "parent":
        return is_path_expr(node.value, names)
    return isinstance(node, ast.Call) and _is_path_call(node, names)


def _is_path_call(node: ast.Call, names: frozenset[str]) -> bool:
    func = node.func
    if _is_path_annotation(func):
        return True
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr in {"cwd", "home"} and _is_path_annotation(func.value):
        return True
    return func.attr in PATH_RETURNING and is_path_expr(func.value, names)


def path_typed_names(tree: ast.AST) -> frozenset[str]:
    """Names annotated as, or assigned from, pathlib paths anywhere in a module."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and _is_path_annotation(node.annotation):
            names.add(node.arg)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and _is_path_annotation(node.annotation)
        ):
            names.add(node.target.id)
    while _add_path_targets(tree, names):
        pass
    return frozenset(names)


def _path_targets(node: ast.Assign | ast.For | ast.comprehension, known: frozenset[str]) -> list[str]:
    value = node.value if isinstance(node, ast.Assign) else node.iter
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    is_path = is_path_expr(value, known) or (
        not isinstance(node, ast.Assign)
        and isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and value.func.attr in {"glob", "iterdir", "rglob"}
        and is_path_expr(value.func.value, known)
    )
    return [target.id for target in targets if is_path and isinstance(target, ast.Name)]


def _add_path_targets(tree: ast.AST, names: set[str]) -> bool:
    """Add names assigned from known paths; return whether any name was new."""
    found = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.For | ast.comprehension)
        for name in _path_targets(node, frozenset(names))
    }
    added = found - names
    names |= added
    return bool(added)


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


def collect_diagnostics(repo_root: Path, paths: Sequence[str] = ()) -> list[Diagnostic]:
    diagnostics = ownership_diagnostics(repo_root)
    selected = [Path(path).as_posix().rstrip("/") for path in paths]
    for path in discover_maintained_python_files(repo_root):
        relpath = repo_relative_path(repo_root, path)
        if selected and not any(
            relpath == item or relpath.startswith(f"{item}/") for item in selected
        ):
            continue
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
    parser.add_argument("paths", nargs="*", help="Repo-relative files or directories to check.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = args.repo_root.resolve()
    diagnostics = collect_diagnostics(repo_root, args.paths)
    if diagnostics:
        print("Python scalability quality check failed:")
        for diagnostic in diagnostics:
            print(diagnostic.render())
        return 1
    print("Python scalability quality check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
