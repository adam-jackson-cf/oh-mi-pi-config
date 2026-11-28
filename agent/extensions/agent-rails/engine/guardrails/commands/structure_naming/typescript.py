from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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

IMPORT_KINDS = frozenset({"import_statement", "export_statement", "call_expression"})
FUNCTION_KINDS = frozenset({"function_declaration", "generator_function_declaration"})
CLASS_KINDS = frozenset({"class_declaration", "abstract_class_declaration"})
FUNCTION_VALUES = frozenset({
    "arrow_function",
    "function_expression",
    "generator_function",
})
TYPE_IDENTIFIERS = frozenset({"type_identifier"})
IDENTIFIERS = frozenset({
    "identifier",
    "property_identifier",
    "private_property_identifier",
    "type_identifier",
    "shorthand_property_identifier",
    "shorthand_property_identifier_pattern",
})


@dataclass(frozen=True)
class ScriptImport:
    specifier: str
    line: int
    type_only: bool


def literal(node: SgNode | None) -> str | None:
    return node.text()[1:-1] if node is not None and node.kind() == "string" else None


def statement_source(node: SgNode) -> SgNode | None:
    clause = next(
        (item for item in named(node) if item.kind() == "import_require_clause"), None
    )
    return node.field("source") if clause is None else clause.field("source")


def call_source(node: SgNode) -> SgNode | None:
    function = node.field("function")
    arguments = node.field("arguments")
    loads = function is not None and (
        function.kind() == "import"
        or (function.kind() == "identifier" and function.text() == "require")
    )
    values = named(arguments) if loads and arguments is not None else []
    return values[0] if values else None


def imports(root: SgNode) -> list[ScriptImport]:
    found: list[ScriptImport] = []
    for node in of_kinds(root, IMPORT_KINDS):
        call = node.kind() == "call_expression"
        specifier = literal(call_source(node) if call else statement_source(node))
        if specifier is None:
            continue
        type_only = not call and any(
            child.kind() == "type" for child in node.children()
        )
        found.append(ScriptImport(specifier, line(node), type_only))
    return found


def declared(statement: SgNode) -> SgNode | None:
    if statement.kind() == "export_statement":
        return statement.field("declaration")
    return statement


def function_values(node: SgNode, offsets: Offsets) -> list[Definition]:
    found = []
    for declarator in named(node):
        name, value = declarator.field("name"), declarator.field("value")
        if (
            name is not None
            and name.kind() == "identifier"
            and value is not None
            and value.kind() in FUNCTION_VALUES
        ):
            found.append(definition(name.text(), offsets, declarator, declarator))
    return found


def class_definitions(node: SgNode, offsets: Offsets) -> list[Definition]:
    name, body = node.field("name"), node.field("body")
    if name is None or body is None:
        return []
    keyword = next(child for child in node.children() if child.kind() != "decorator")
    found = [definition(name.text(), offsets, keyword, node)]
    for member in named(body):
        method = member.field("name")
        if member.kind() == "method_definition" and method is not None:
            found.append(
                definition(f"{name.text()}.{method.text()}", offsets, member, member)
            )
    return found


def definitions(root: SgNode, source: str) -> list[Definition]:
    offsets = Offsets(source)
    found: list[Definition] = []
    for statement in named(root):
        node = declared(statement)
        if node is None:
            continue
        name = node.field("name")
        if node.kind() in FUNCTION_KINDS and name is not None:
            found.append(definition(name.text(), offsets, node, node))
        elif node.kind() in CLASS_KINDS:
            found.extend(class_definitions(node, offsets))
        elif node.kind() == "lexical_declaration":
            found.extend(function_values(node, offsets))
    return found


def comment_spans(root: SgNode, source: str) -> list[Span]:
    return spans(source, root.find_all(kind="comment"))


def identifiers(root: SgNode, grammar: str) -> list[str]:
    kinds = IDENTIFIERS - TYPE_IDENTIFIERS if grammar == "javascript" else IDENTIFIERS
    return unique_texts(root, kinds)
