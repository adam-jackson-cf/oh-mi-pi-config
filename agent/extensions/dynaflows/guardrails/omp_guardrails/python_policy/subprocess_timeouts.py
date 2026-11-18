"""TEST.subprocess-timeouts: every process a test starts or waits on is bounded.

``omp-guardrails python-test-hygiene [path ...]`` checks the repository's Python test files:
files matching ``layout.test_file_patterns`` (the house-style defaults when the profile has
none), files under a tier ``location`` and ``conftest.py`` files. Paths excluded by
``guardrails.python.excluded_paths`` and generated trees are skipped.

A finding is any of:

- ``subprocess.run``, ``call``, ``check_call`` or ``check_output`` without a positive
  ``timeout=``;
- ``wait()`` or ``communicate()`` on a ``subprocess.Popen`` without a positive timeout;
- ``wait()`` or ``communicate()`` on an ``asyncio`` subprocess outside a positive
  ``asyncio.wait_for``.

Imports and aliases are resolved, so unrelated ``run`` or ``wait`` methods are not flagged.
A timeout given as a name or expression counts as positive; only a literal zero or negative
value does not. An unparsable test file is a finding too. A finding is waived only by an
ADR-backed ``house_style.exceptions`` entry for this rule.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

from omp_guardrails.config import house_style
from omp_guardrails.diffs.timeouts import tier_locations
from omp_guardrails.findings import Finding
from omp_guardrails.python_policy.scope import (
    finding,
    inventory_finding,
    load_settings,
    parse_paths,
    python_files,
    read_source,
)
from omp_guardrails.structure.inventory import InventoryError, matches_any
from omp_guardrails.structure.naming import is_test_file

RULE = "TEST.subprocess-timeouts"
FIX = "supply a positive timeout; bound async waits with asyncio.wait_for"
SYNCHRONOUS = frozenset(
    f"subprocess.{method}" for method in ("run", "call", "check_call", "check_output")
)
PROCESS_FACTORIES = frozenset(
    {"subprocess.Popen", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}
)
PROCESS_ANNOTATIONS = frozenset({"subprocess.Popen", "asyncio.subprocess.Process"})


def positive_timeout(node: ast.AST | None) -> bool:
    """A literal timeout must be positive; a name or expression is trusted."""
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        value = node.value
        return isinstance(value, int | float) and not isinstance(value, bool) and value > 0
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant):
        if isinstance(node.op, ast.USub) and isinstance(node.operand.value, int | float):
            return -node.operand.value > 0
        return positive_timeout(node.operand)
    return True


def keyword_timeout(call: ast.Call) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == "timeout"), None)


class ProcessWaits(ast.NodeVisitor):
    """Collect the lines of unbounded process calls, resolving imports and process variables."""

    def __init__(self) -> None:
        self.imports: dict[str, str] = {}
        self.processes: dict[str, str] = {}
        self.bounded: set[int] = set()
        self.lines: list[int] = []

    def name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.imports.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{self.name(node.value)}.{node.attr}"
        return ""

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports[alias.asname or alias.name] = alias.name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.imports[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        imports, processes = self.imports, self.processes
        self.imports, self.processes = imports.copy(), processes.copy()
        for argument in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            self.processes.pop(argument.arg, None)
            self.imports.pop(argument.arg, None)
            annotation = self.name(argument.annotation) if argument.annotation else ""
            if annotation in PROCESS_ANNOTATIONS:
                self.processes[argument.arg] = annotation
        self.generic_visit(node)
        self.imports, self.processes = imports, processes

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def assign(self, target: ast.AST, value: ast.AST | None) -> None:
        if not isinstance(target, ast.Name | ast.Attribute):
            return
        if isinstance(value, ast.Await):
            value = value.value
        kind = self.name(value.func) if isinstance(value, ast.Call) else ""
        if kind in PROCESS_FACTORIES:
            self.processes[ast.unparse(target)] = kind
        else:
            self.processes.pop(ast.unparse(target), None)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.assign(node.target, node.value)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self.assign(target, node.value)
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            if item.optional_vars is not None:
                self.assign(item.optional_vars, item.context_expr)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = self.name(node.func)
        if name == "asyncio.wait_for":
            timeout = keyword_timeout(node) or (node.args[1] if len(node.args) > 1 else None)
            future = (
                node.args[0]
                if node.args
                else next((item.value for item in node.keywords if item.arg == "fut"), None)
            )
            if positive_timeout(timeout) and future is not None:
                self.bounded.update(id(child) for child in ast.walk(future))
        process = ""
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"wait", "communicate"}:
            if isinstance(node.func.value, ast.Name | ast.Attribute):
                process = self.processes.get(ast.unparse(node.func.value), "")
            elif isinstance(node.func.value, ast.Call):
                constructor = self.name(node.func.value.func)
                process = constructor if constructor == "subprocess.Popen" else ""
        if (name in SYNCHRONOUS or process) and not self.is_bounded(node, process):
            self.lines.append(node.lineno)
        self.generic_visit(node)

    def is_bounded(self, node: ast.Call, process: str) -> bool:
        if process.startswith("asyncio."):
            return id(node) in self.bounded
        timeout = keyword_timeout(node)
        if timeout is None and process and isinstance(node.func, ast.Attribute):
            position = 0 if node.func.attr == "wait" else 1
            timeout = node.args[position] if len(node.args) > position else None
        return positive_timeout(timeout)


def is_test_source(path: str, profile: dict) -> bool:
    if PurePosixPath(path).name == "conftest.py":
        return True
    return is_test_file(path, profile) or matches_any(path, tier_locations(profile))


def unbounded_lines(tree: ast.AST) -> list[int]:
    visitor = ProcessWaits()
    visitor.visit(tree)
    return visitor.lines


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails python-test-hygiene [path ...]``."""
    selected = parse_paths("python-test-hygiene", argv)
    try:
        files = python_files(repo, load_settings(profile), selected)
    except InventoryError as error:
        return [inventory_finding(RULE, error)]
    found: list[Finding] = []
    for relpath in files:
        if not is_test_source(relpath, profile):
            continue
        source = read_source(repo, relpath)
        if source.tree is None:
            found.append(finding(RULE, relpath, None, source.error, "repair the Python syntax"))
            continue
        found.extend(
            finding(RULE, relpath, line, "process call or wait has no positive timeout", FIX)
            for line in unbounded_lines(source.tree)
        )
    return house_style.apply_exceptions(found, profile)
