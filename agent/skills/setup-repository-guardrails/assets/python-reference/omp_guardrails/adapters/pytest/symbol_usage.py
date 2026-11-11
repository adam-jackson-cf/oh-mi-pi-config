"""TEST.no-test-hooks: production symbols that only tests use.

A production symbol is a public (no leading underscore) top-level function or class defined in
a production file of an owned package. It is a test-only symbol when at least one test file
names it and no production file does, apart from its own definition. A name counts as used
where it appears as a name, an attribute, an import or a string in ``__all__``. Matching is by
name, not by resolved type, so a symbol whose name any production file mentions is never
reported; the check errs towards silence.

Symbols the profile's ``public_entry_points`` name (``package.module:symbol``) are the declared
public interface and never count as test-only.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from omp_guardrails.findings import Finding

RULE = "TEST.no-test-hooks"
FIX = (
    "drive the behaviour through a public entry point and delete the symbol, or give it a "
    "production caller; production code never carries hooks only tests use"
)


DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class Definition:
    path: str
    name: str
    line: int


def parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def definitions(path: str, tree: ast.Module) -> list[Definition]:
    return [
        Definition(path, node.name, node.lineno)
        for node in tree.body
        if isinstance(node, DEFINITIONS) and not node.name.startswith("_")
    ]


def used_names(tree: ast.Module, *, skip: frozenset[int] = frozenset()) -> set[str]:
    """Names the module uses; ``skip`` holds ``id()`` of definition nodes to leave out."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.update(alias.name.split("."))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
        elif isinstance(node, DEFINITIONS) and id(node) not in skip:
            names.add(node.name)
    return names


def entry_point_symbols(profile: dict) -> set[str]:
    entries = profile.get("public_entry_points")
    symbols = set()
    for value in entries.values() if isinstance(entries, dict) else []:
        text = str(value)
        if ":" in text:
            symbols.add(text.rsplit(":", 1)[1].split(".")[0].strip())
        else:
            symbols.add(text.rsplit(".", 1)[-1].strip())
    return symbols


def test_only_findings(
    production: dict[str, str], tests: dict[str, str], profile: dict, reported: set[str]
) -> list[Finding]:
    """Findings for test-only symbols defined in the ``reported`` production paths."""
    defined: list[Definition] = []
    production_uses: set[str] = set()
    for path, source in production.items():
        tree = parse(source)
        if tree is None:
            continue
        own = frozenset(id(node) for node in tree.body if isinstance(node, DEFINITIONS))
        production_uses |= used_names(tree, skip=own)
        if path in reported:
            defined.extend(definitions(path, tree))
    test_uses: dict[str, str] = {}
    for path, source in sorted(tests.items()):
        tree = parse(source)
        for name in used_names(tree) if tree is not None else set():
            test_uses.setdefault(name, path)
    public = entry_point_symbols(profile)
    return [
        Finding(
            RULE,
            item.path,
            f"line {item.line}: production symbol '{item.name}' is used only by tests "
            f"({test_uses[item.name]})",
            FIX,
        )
        for item in defined
        if item.name in test_uses and item.name not in production_uses and item.name not in public
    ]
