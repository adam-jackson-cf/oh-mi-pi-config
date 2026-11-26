from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from guardrails.checker.growth import behaviour_name
from guardrails.commands.adapters.go.syntax import test_functions, walk
from guardrails.foundation.source.go import STRING_KINDS, TEST_NAME
from guardrails.foundation.source.tree import SourceError, parse

if TYPE_CHECKING:
    from ast_grep_py import SgNode

SUFFIXES = (".go",)
COMMENT = "comment"
LOCAL = "identifier"
NAME_DECLARERS = frozenset({
    "parameter_declaration",
    "variadic_parameter_declaration",
    "var_spec",
    "const_spec",
})
LIST_DECLARERS = frozenset({"short_var_declaration", "range_clause"})


class CollectionError(ValueError):
    pass


@dataclass(frozen=True)
class Case:
    selector: str
    fingerprint: str


@dataclass(frozen=True)
class Collected:
    cases: tuple[Case, ...]
    behaviours: Mapping[str, str | None]


def behaviour(name: str) -> str:
    match = TEST_NAME.match(name)
    return behaviour_name(name, match.group(1) if match else "")


def declared_names(node: SgNode) -> list[str]:
    if node.kind() in NAME_DECLARERS:
        return [name.text() for name in node.field_children("name")]
    left = node.field("left") if node.kind() in LIST_DECLARERS else None
    return (
        []
        if left is None
        else [item.text() for item in walk(left) if item.kind() == LOCAL]
    )


def local_names(function: SgNode) -> dict[str, str]:
    """The test's parameters and the names its body declares, each by its order."""
    names: dict[str, str] = {}
    for node in walk(function):
        for name in declared_names(node):
            names.setdefault(name, f"local{len(names)}")
    return names


def tokens(node: SgNode, local: Mapping[str, str]) -> list[str]:
    """The body's tokens without comments, local names replaced by their order."""
    if node.kind() == COMMENT:
        return []
    if node.kind() in STRING_KINDS:
        return [node.text()]
    children = node.children()
    if not children:
        return [
            local.get(node.text(), node.text()) if node.kind() == LOCAL else node.text()
        ]
    return [token for child in children for token in tokens(child, local)]


def fingerprint(function: SgNode, body: SgNode) -> str:
    """The test body's calls and assertions, independent of local names and comments."""
    text = " ".join(tokens(body, local_names(function)))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def collect(path: str, text: str) -> Collected:
    try:
        root = parse(path, text)
    except SourceError as error:
        raise CollectionError(f"{path} does not parse as Go") from error
    cases = []
    behaviours: dict[str, str | None] = {}
    for function in test_functions(root):
        name_node = function.field("name")
        body = function.field("body")
        if name_node is None or body is None:
            continue
        selector = f"{path}::{name_node.text()}"
        if selector in behaviours:
            raise CollectionError(f"duplicate test function: {selector}")
        behaviours[selector] = behaviour(name_node.text())
        cases.append(Case(selector, fingerprint(function, body)))
    return Collected(tuple(cases), behaviours)
