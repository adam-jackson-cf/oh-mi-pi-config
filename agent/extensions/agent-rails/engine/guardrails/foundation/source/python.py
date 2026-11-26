from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from guardrails.foundation.source.tree import (
    Definition,
    Offsets,
    Span,
    definition,
    line,
    named,
    of_kinds,
    spans,
    unique_texts,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

IMPORT_KINDS = frozenset({
    "import_statement",
    "import_from_statement",
    "future_import_statement",
})
DOCUMENTED = frozenset({"function_definition", "class_definition"})
TEXT_KINDS = frozenset({"string", "concatenated_string"})
NON_TEXT_PREFIXES = frozenset("bBfFtT")
TRAILING_KINDS = frozenset({"comment", "line_continuation"})
IDENTIFIERS = frozenset({"identifier"})


@dataclass(frozen=True)
class PythonImport:
    statement: Literal["import", "from"]
    module: str
    names: tuple[str, ...]
    level: int
    line: int


def dotted(node: SgNode) -> str:
    return ".".join(part.text() for part in named(node) if part.kind() == "identifier")


def imported_name(node: SgNode) -> str:
    target = node.field("name") if node.kind() == "aliased_import" else node
    return dotted(target) if target is not None else ""


def from_import(node: SgNode) -> PythonImport:
    source = node.field("module_name")
    if source is None:
        module, level = "__future__", 0
    elif source.kind() == "relative_import":
        parts = named(source)
        prefix = next(part for part in parts if part.kind() == "import_prefix")
        path = [part for part in parts if part.kind() == "dotted_name"]
        module, level = (dotted(path[0]) if path else ""), len(prefix.text())
    else:
        module, level = dotted(source), 0
    names = tuple(imported_name(item) for item in node.field_children("name"))
    wildcard = any(item.kind() == "wildcard_import" for item in named(node))
    return PythonImport(
        "from", module, ("*",) if wildcard else names, level, line(node)
    )


def imports(root: SgNode) -> list[PythonImport]:
    found: list[PythonImport] = []
    for node in of_kinds(root, IMPORT_KINDS):
        if node.kind() == "import_statement":
            found.extend(
                PythonImport("import", imported_name(item), (), 0, line(node))
                for item in node.field_children("name")
            )
        else:
            found.append(from_import(node))
    return found


def last_token(node: SgNode) -> SgNode:
    while True:
        tail = [
            child for child in node.children() if child.kind() not in TRAILING_KINDS
        ]
        if not tail:
            return node
        node = tail[-1]


def defined(node: SgNode) -> SgNode:
    inner = node.field("definition") if node.kind() == "decorated_definition" else None
    return node if inner is None else inner


def named_definition(name: str, offsets: Offsets, node: SgNode) -> Definition:
    return definition(name, offsets, node, last_token(node))


def definitions(root: SgNode, source: str) -> list[Definition]:
    offsets = Offsets(source)
    found: list[Definition] = []
    for statement in named(root):
        node = defined(statement)
        name = node.field("name")
        if node.kind() not in DOCUMENTED or name is None:
            continue
        found.append(named_definition(name.text(), offsets, node))
        body = node.field("body")
        if node.kind() != "class_definition" or body is None:
            continue
        for member in map(defined, named(body)):
            method = member.field("name")
            if member.kind() == "function_definition" and method is not None:
                found.append(
                    named_definition(f"{name.text()}.{method.text()}", offsets, member)
                )
    return found


def text_literal(node: SgNode) -> bool:
    while node.kind() == "parenthesized_expression" and len(named(node)) == 1:
        node = named(node)[0]
    if node.kind() not in TEXT_KINDS:
        return False
    parts = named(node) if node.kind() == "concatenated_string" else [node]
    return all(
        part.kind() == "string"
        and not NON_TEXT_PREFIXES.intersection(named(part)[0].text().rstrip("'\""))
        for part in parts
    )


def docstring(body: SgNode) -> SgNode | None:
    statements = named(body)
    first = statements[0] if statements else None
    if first is None or first.kind() != "expression_statement":
        return None
    values = named(first)
    return first if len(values) == 1 and text_literal(values[0]) else None


def docstring_spans(root: SgNode, source: str) -> list[Span]:
    bodies = [root, *(node.field("body") for node in of_kinds(root, DOCUMENTED))]
    found = [
        statement
        for body in bodies
        if body is not None and (statement := docstring(body)) is not None
    ]
    return spans(source, found)


def comment_spans(root: SgNode, source: str) -> list[Span]:
    return spans(source, root.find_all(kind="comment"))


def identifiers(root: SgNode) -> list[str]:
    return unique_texts(root, IDENTIFIERS)
