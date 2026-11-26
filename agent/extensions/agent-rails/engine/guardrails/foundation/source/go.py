from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from guardrails.foundation.source.tree import (
    Definition,
    Offsets,
    SourceError,
    Span,
    definition,
    line,
    named,
    spans,
    unique_texts,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

TEST_SUFFIX = "_test.go"
TEST_NAME = re.compile(r"^(Test|Fuzz|Example)(?![a-z])\w*$")
EMBED = re.compile(r"//go:embed\s(.*)")
BUILD = re.compile(r"//go:build\s(.*)")
GO_STRING = re.compile(
    r'"(?:[^"\\\n]|\\(?:[abfnrtv\\"]|[0-7]{3}|x[0-9A-Fa-f]{2}'
    r'|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}))*"'
)
EMBED_PATTERN = re.compile(r'"(?:[^"\\]|\\.)*"|`[^`]*`|\S+')
RECEIVER_WRAPPERS = frozenset({"pointer_type", "generic_type", "parenthesized_type"})
STRING_KINDS = frozenset({"interpreted_string_literal", "raw_string_literal"})
IDENTIFIERS = frozenset({
    "identifier",
    "field_identifier",
    "type_identifier",
    "package_identifier",
})


@dataclass(frozen=True)
class GoImport:
    path: str
    line: int


def imports(root: SgNode) -> list[GoImport]:
    return [
        GoImport(path.text()[1:-1], line(node))
        for node in root.find_all(kind="import_spec")
        if (path := node.field("path")) is not None and path.kind() in STRING_KINDS
    ]


def package_clause(root: SgNode) -> SgNode:
    clause = next(
        (node for node in named(root) if node.kind() == "package_clause"), None
    )
    if clause is None:
        raise SourceError("Go source has no package clause")
    return clause


def package_name(root: SgNode) -> str:
    return named(package_clause(root))[0].text()


def receiver_type(node: SgNode) -> str:
    receiver = node.field("receiver")
    parameters = named(receiver) if receiver is not None else []
    kind = parameters[0].field("type") if parameters else None
    while kind is not None and kind.kind() in RECEIVER_WRAPPERS:
        kind = named(kind)[0]
    if kind is None:
        raise SourceError(f"line {line(node)}: method has no receiver type")
    return kind.text()


def definitions(root: SgNode, source: str) -> list[Definition]:
    offsets = Offsets(source)
    found: list[Definition] = []
    for node in named(root):
        name = node.field("name")
        if name is None:
            continue
        if node.kind() == "function_declaration":
            found.append(definition(name.text(), offsets, node, node))
        elif node.kind() == "method_declaration":
            qualified = f"{receiver_type(node)}.{name.text()}"
            found.append(definition(qualified, offsets, node, node))
    return found


def comment_spans(root: SgNode, source: str) -> list[Span]:
    return spans(source, root.find_all(kind="comment"))


def identifiers(root: SgNode) -> list[str]:
    return unique_texts(root, IDENTIFIERS)


def embed_value(token: str) -> str:
    if token.startswith("`"):
        return token[1:-1]
    if token.startswith('"'):
        if GO_STRING.fullmatch(token) is None:
            raise SourceError(f"invalid //go:embed pattern {token}")
        return str(ast.literal_eval(token))
    return token


def embed_patterns(root: SgNode) -> list[str]:
    return [
        embed_value(token)
        for node in root.find_all(kind="comment")
        if (directive := EMBED.fullmatch(node.text())) is not None
        for token in EMBED_PATTERN.findall(directive.group(1))
    ]


def build_ignored(root: SgNode) -> bool:
    clause = package_clause(root).range().start.index
    return any(
        node.kind() == "comment"
        and node.range().end.index <= clause
        and (constraint := BUILD.fullmatch(node.text())) is not None
        and constraint.group(1).strip() == "ignore"
        for node in root.children()
    )


def is_test_file(path: str) -> bool:
    return path.endswith(TEST_SUFFIX)
