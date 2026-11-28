from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters.pytest.syntax import (
    IMPORT_KINDS,
    NAMES,
    STRING_KINDS,
    Role,
    format_texts,
    import_aliases,
    string_texts,
    walk,
)
from guardrails.context.config.reader import tests_settings
from guardrails.foundation.findings import Finding
from guardrails.foundation.source.python import defined
from guardrails.foundation.source.tree import SourceError, line, named, parse

if TYPE_CHECKING:
    from ast_grep_py import SgNode

RULE = "TEST.no-test-hooks"
FIX = (
    "drive the behaviour through a public entry point and delete the symbol, or give it a "
    "production caller; production code never carries hooks only tests use"
)


DEFINITIONS = frozenset({"function_definition", "class_definition"})
USED = NAMES | {Role.ATTRIBUTE, Role.FUNCTION, Role.CLASS}


@dataclass(frozen=True)
class Definition:
    path: str
    name: str
    line: int


def parsed(path: str, source: str) -> SgNode | None:
    try:
        return parse(path, source)
    except SourceError:
        return None


def top_level(root: SgNode) -> list[tuple[SgNode, SgNode]]:
    return [
        (node, name)
        for node in map(defined, named(root))
        if node.kind() in DEFINITIONS and (name := node.field("name")) is not None
    ]


def definitions(path: str, root: SgNode) -> list[Definition]:
    return [
        Definition(path, name.text(), line(node))
        for node, name in top_level(root)
        if not name.text().startswith("_")
    ]


def node_names(node: SgNode, role: Role, skip: frozenset[SgNode]) -> list[str]:
    kind = node.kind()
    if kind == "identifier":
        return [node.text()] if role in USED and node not in skip else []
    if kind in IMPORT_KINDS:
        return [
            part
            for _, _, original in import_aliases(node)
            for part in original.split(".")
        ]
    if kind == "format_specifier":
        return format_texts(node)
    parent = node.parent() if kind == "string" else None
    if kind in STRING_KINDS and (
        parent is None or parent.kind() != "concatenated_string"
    ):
        return string_texts(node)
    return []


def used_names(root: SgNode, *, skip: frozenset[SgNode] = frozenset()) -> set[str]:
    names: set[str] = set()
    for node, role in walk(root):
        names.update(node_names(node, role, skip))
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
    defined_symbols: list[Definition] = []
    production_uses: set[str] = set()
    for path, source in production.items():
        root = parsed(path, source)
        if root is None:
            continue
        own = frozenset(name for _, name in top_level(root))
        production_uses |= used_names(root, skip=own)
        if path in reported:
            defined_symbols.extend(definitions(path, root))
    test_uses: dict[str, str] = {}
    for path, source in sorted(tests.items()):
        root = parsed(path, source)
        for name in used_names(root) if root is not None else set():
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
        for item in defined_symbols
        if item.name in test_uses
        and item.name not in production_uses
        and item.name not in public
    ]
