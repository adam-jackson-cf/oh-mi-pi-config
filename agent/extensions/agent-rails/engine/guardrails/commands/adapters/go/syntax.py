"""Go source syntax: parsing, test functions, imports and identifier words."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from guardrails.checker.growth import name_words
from guardrails.foundation.source import go, tree
from guardrails.foundation.source.go import STRING_KINDS, TEST_NAME
from guardrails.foundation.source.tree import named

if TYPE_CHECKING:
    from ast_grep_py import SgNode


def walk(node: SgNode) -> Iterator[SgNode]:
    yield node
    for item in named(node):
        yield from walk(item)


def string_value(node: SgNode | None) -> str | None:
    if node is None or node.kind() not in STRING_KINDS:
        return None
    return node.text()[1:-1]


def words(name: str) -> list[str]:
    return name_words(name)


def test_functions(root: SgNode) -> list[SgNode]:
    return [
        node
        for node in named(root)
        if node.kind() == "function_declaration"
        and (name := node.field("name")) is not None
        and TEST_NAME.match(name.text())
    ]


def first_parameter(function: SgNode) -> str | None:
    parameters = function.field("parameters")
    for declaration in named(parameters) if parameters is not None else []:
        name = declaration.field("name")
        if name is not None:
            return name.text()
    return None


def call_name(call: SgNode) -> tuple[str | None, str | None]:
    function = call.field("function")
    if function is None:
        return None, None
    if function.kind() == "selector_expression":
        operand, field = function.field("operand"), function.field("field")
        return (
            operand.text() if operand is not None else None,
            field.text() if field is not None else None,
        )
    if function.kind() == "identifier":
        return None, function.text()
    return None, None


def arguments(call: SgNode) -> list[SgNode]:
    node = call.field("arguments")
    return named(node) if node is not None else []


def go_files(paths: list[str]) -> list[str]:
    """The paths the go tool reads as package source (go.built_path)."""
    return sorted({path for path in paths if go.built_path(path)})


def defined_tests(path: str, text: str) -> list[str]:
    return [
        name.text()
        for function in test_functions(tree.parse(path, text))
        if (name := function.field("name")) is not None
    ]
