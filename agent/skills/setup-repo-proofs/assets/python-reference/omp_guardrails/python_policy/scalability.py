"""PYS rules: bounded loop depth and seam-only effects in analyzer-like code.

``omp-guardrails python-scalability [path ...]`` checks maintained Python files. Profile
``guardrails.python`` settings (patterns as in ``scope``):

- ``maintained_roots``: the files checked at all.
- ``loop_roots`` (default ``maintained_roots``) and ``max_loop_depth`` (default 2): loops and
  comprehension generators nest at most this deep; each function or lambda body starts at
  zero (PYS250).
- ``analyzer_like_roots``: code that must reach the filesystem (PYS251), processes and the
  environment (PYS252) only through injected seams. Unset means no analyzer-like scope is
  configured, not that the rules are inapplicable.
- ``ownership_markers``: paths of which at least one must exist while analyzer-like roots are
  configured (PYS253).

An unparsable file is PYS000.
"""

from __future__ import annotations

import ast
from pathlib import Path

from omp_guardrails.config import house_style
from omp_guardrails.findings import Finding
from omp_guardrails.python_policy.call_names import PROCESS_CALLS, import_names, qualified_name
from omp_guardrails.python_policy.scope import (
    PROFILE_PATH,
    finding,
    in_roots,
    inventory_finding,
    load_settings,
    parse_paths,
    python_files,
    read_source,
)
from omp_guardrails.structure.inventory import InventoryError

DEFAULT_MAX_LOOP_DEPTH = 2
FILESYSTEM_METHODS = frozenset({"read_bytes", "read_text", "write_bytes", "write_text"})
ENVIRONMENT_READS = frozenset({"os.getenv", "os.environ.get", "os.environ.copy"})
SEAM_FIX = "inject the effect through a seam owned outside the analyzer-like roots"


class ScalabilityVisitor(ast.NodeVisitor):
    """Walks one module, tracking loop depth per callable body and the enclosing function."""

    def __init__(self, relpath: str, *, max_depth: int | None, analyzer_like: bool) -> None:
        self.relpath = relpath
        self.max_depth = max_depth
        self.analyzer_like = analyzer_like
        self.findings: list[Finding] = []
        self.names: dict[str, str] = {}
        self._functions: list[str] = []
        self._depth = 0

    def visit_Module(self, node: ast.Module) -> None:
        self.names = import_names(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_callable(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_callable(node, node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        outer, self._depth = self._depth, 0
        self.visit(node.body)
        self._depth = outer

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

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, (node.key, node.value))

    def visit_Call(self, node: ast.Call) -> None:
        if self.analyzer_like:
            self._check_call(node)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if self.analyzer_like and is_environ(node.value, self.names):
            verb = "read" if isinstance(node.ctx, ast.Load) else "mutation"
            self._seam("PYS252", node.lineno, f"direct os.environ {verb}")
        self.generic_visit(node)

    def _check_call(self, node: ast.Call) -> None:
        name = qualified_name(node.func, self.names)
        if name in {"open", "io.open"} or (
            isinstance(node.func, ast.Attribute) and node.func.attr in FILESYSTEM_METHODS
        ):
            self._seam("PYS251", node.lineno, "synchronous filesystem I/O")
        if name in PROCESS_CALLS:
            self._seam("PYS252", node.lineno, f"direct process call {name}")
        if name in ENVIRONMENT_READS:
            self._seam("PYS252", node.lineno, f"direct environment read {name}")

    def _seam(self, rule: str, line: int, what: str) -> None:
        self.findings.append(
            finding(rule, self.relpath, line, f"{what} in analyzer-like code in {self._owner()}", SEAM_FIX)
        )

    def _owner(self) -> str:
        return self._functions[-1] if self._functions else "<module>"

    def _visit_callable(self, node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        self._functions.append(name)
        outer, self._depth = self._depth, 0
        for statement in node.body:
            self.visit(statement)
        self._depth = outer
        self._functions.pop()

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self.visit(node.iter)
        self._enter_loop(node.lineno)
        self.visit(node.test if isinstance(node, ast.While) else node.target)
        for statement in node.body:
            self.visit(statement)
        self._depth -= 1
        for statement in node.orelse:
            self.visit(statement)

    def _visit_comprehension(
        self, generators: list[ast.comprehension], results: tuple[ast.expr, ...], index: int = 0
    ) -> None:
        if index == len(generators):
            for result in results:
                self.visit(result)
            return
        generator = generators[index]
        self.visit(generator.iter)
        self._enter_loop(generator.target.lineno)
        self.visit(generator.target)
        for condition in generator.ifs:
            self.visit(condition)
        self._visit_comprehension(generators, results, index + 1)
        self._depth -= 1

    def _enter_loop(self, line: int) -> None:
        self._depth += 1
        if self.max_depth is not None and self._depth == self.max_depth + 1:
            self.findings.append(
                finding(
                    "PYS250",
                    self.relpath,
                    line,
                    f"loop depth {self._depth} exceeds {self.max_depth} in {self._owner()}",
                    "extract the inner loop into an independently named computation",
                )
            )


def is_environ(node: ast.AST, names: dict[str, str]) -> bool:
    return qualified_name(node, names) == "os.environ"


def ownership_findings(repo: Path, config) -> list[Finding]:
    roots = config.paths("analyzer_like_roots")
    if not roots or any((repo / marker).exists() for marker in config.paths("ownership_markers")):
        return []
    return [
        finding(
            "PYS253",
            PROFILE_PATH,
            None,
            f"analyzer-like roots {', '.join(roots)} have no existing ownership marker",
            "add the owner file and list it in guardrails.python.ownership_markers",
        )
    ]


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    selected = parse_paths("python-scalability", argv)
    config = load_settings(profile)
    try:
        files = python_files(repo, config, selected)
    except InventoryError as error:
        return [inventory_finding("PYS000", error)]
    max_depth = config.number("", "max_loop_depth", DEFAULT_MAX_LOOP_DEPTH)
    loop_roots = config.scope("loop_roots")
    analyzer_roots = config.paths("analyzer_like_roots")
    found = ownership_findings(repo, config)
    for relpath in files:
        if not in_roots(relpath, config.paths("maintained_roots")):
            continue
        source = read_source(repo, relpath)
        if source.tree is None:
            found.append(finding("PYS000", relpath, None, source.error, "restore readable UTF-8 Python"))
            continue
        visitor = ScalabilityVisitor(
            relpath,
            max_depth=max_depth if in_roots(relpath, loop_roots) else None,
            analyzer_like=in_roots(relpath, analyzer_roots),
        )
        visitor.visit(source.tree)
        found.extend(visitor.findings)
    return house_style.apply_exceptions(found, profile)
