from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Any

from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import tests_settings

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


def entry_point_symbols(config: dict[str, Any]) -> set[str]:
    entries = tests_settings(config, "public_entry_points")
    symbols = set()
    for value in entries.values() if isinstance(entries, dict) else []:
        text = str(value)
        if ":" in text:
            symbols.add(text.rsplit(":", 1)[1].split(".")[0].strip())
        else:
            symbols.add(text.rsplit(".", 1)[-1].strip())
    return symbols


def test_only_findings(
    production: dict[str, str],
    tests: dict[str, str],
    config: dict[str, Any],
    reported: set[str],
) -> list[Finding]:
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
    public = entry_point_symbols(config)
    return [
        Finding(
            RULE,
            item.path,
            f"line {item.line}: production symbol '{item.name}' is used only by tests "
            f"({test_uses[item.name]})",
            FIX,
        )
        for item in defined
        if item.name in test_uses
        and item.name not in production_uses
        and item.name not in public
    ]
