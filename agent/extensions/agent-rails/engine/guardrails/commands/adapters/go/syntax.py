"""Go source syntax: parsing, test functions, imports and identifier words."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ast_grep_py import SgNode

SOURCE_SUFFIX = ".go"
TEST_SUFFIX = "_test.go"
SKIPPED_PARTS = frozenset({".git", "vendor", "testdata", "node_modules"})
TEST_NAME = re.compile(r"^(Test|Fuzz|Example)(?![a-z])\w*$")
TEST_DEFINITION = re.compile(r"^func\s+((?:Test|Fuzz|Example)(?![a-z])\w*)\s*\(", re.M)
VERSION_ELEMENT = re.compile(r"^v\d+$")
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def parse(text: str) -> SgNode:
    from ast_grep_py import SgRoot

    return SgRoot(text, "go").root()


def has_errors(root: SgNode) -> bool:
    return root.find(kind="ERROR") is not None


def children(node: SgNode) -> list[SgNode]:
    return [
        item for item in node.children() if item.is_named() and item.kind() != "comment"
    ]


def walk(node: SgNode) -> Iterator[SgNode]:
    yield node
    for item in children(node):
        yield from walk(item)


def string_value(node: SgNode | None) -> str | None:
    if node is None:
        return None
    if node.kind() in {"interpreted_string_literal", "raw_string_literal"}:
        return node.text()[1:-1]
    return None


def default_alias(path: str) -> str:
    elements = path.split("/")
    name = elements[-1]
    if VERSION_ELEMENT.match(name) and len(elements) > 1:
        name = elements[-2]
    name = name.split(".")[0].removeprefix("go-").removesuffix("-go")
    return name.replace("-", "")


def imports(root: SgNode) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in walk(root):
        if node.kind() != "import_spec":
            continue
        path = string_value(node.field("path"))
        if path is None:
            continue
        name = node.field("name")
        alias = name.text() if name is not None else default_alias(path)
        if alias not in {".", "_"}:
            found[alias] = path
    return found


def words(name: str) -> list[str]:
    return [item.lower() for item in WORD.findall(name)]


def test_functions(root: SgNode) -> list[SgNode]:
    return [
        node
        for node in children(root)
        if node.kind() == "function_declaration"
        and (name := node.field("name")) is not None
        and TEST_NAME.match(name.text())
    ]


def first_parameter(function: SgNode) -> str | None:
    parameters = function.field("parameters")
    for declaration in children(parameters) if parameters is not None else []:
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
    return children(node) if node is not None else []


def is_test_path(path: str) -> bool:
    return path.endswith(TEST_SUFFIX)


def go_files(paths: list[str]) -> list[str]:
    return sorted({
        path
        for path in paths
        if path.endswith(SOURCE_SUFFIX)
        and not SKIPPED_PARTS.intersection(PurePosixPath(path).parts)
    })


def defined_tests(text: str) -> list[str]:
    return TEST_DEFINITION.findall(text)
