from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.foundation.source.tree import (
    Definition,
    Offsets,
    SourceError,
    Span,
    definition,
    language_pack,
    languages,
    line,
    named,
    of_kinds,
    spans,
    unique_texts,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

TEST_NAME = re.compile(r"^(Test|Fuzz|Example)(?![a-z])\w*$")
EMBED = re.compile(r"//go:embed\s(.*)")
BUILD = re.compile(r"//go:build\s(.*)")
GO_STRING = re.compile(
    r'"(?:[^"\\\n]|\\(?:[abfnrtv\\"]|[0-7]{3}|x[0-9A-Fa-f]{2}'
    r'|u[0-9A-Fa-f]{4}|U[0-9A-Fa-f]{8}))*"'
)
EMBED_PATTERN = re.compile(r'"(?:[^"\\]|\\.)*"|`[^`]*`|\S+')
VERSION_ELEMENT = re.compile(r"^v\d+$")
HIDDEN_ALIASES = frozenset({".", "_"})
RECEIVER_WRAPPERS = frozenset({"pointer_type", "generic_type", "parenthesized_type"})
STRING_KINDS = frozenset({"interpreted_string_literal", "raw_string_literal"})
QUALIFIED_KINDS = frozenset({"selector_expression", "qualified_type"})
LOCAL_NAMES = frozenset({"identifier", "type_identifier"})
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


@dataclass(frozen=True)
class GoReference:
    """A package-level name a Go file reads, at a byte offset: through the import path of
    a package it imports, or (import_path None) in the file's own package."""

    import_path: str | None
    name: str
    at: int


def imports(root: SgNode) -> list[GoImport]:
    return [
        GoImport(path.text()[1:-1], line(node))
        for node in root.find_all(kind="import_spec")
        if (path := node.field("path")) is not None and path.kind() in STRING_KINDS
    ]


def default_alias(path: str) -> str:
    """The name an import binds without an alias: its last path element, past a major
    version element and the usual go- and -go affixes."""
    elements = path.split("/")
    name = elements[-1]
    if VERSION_ELEMENT.match(name) and len(elements) > 1:
        name = elements[-2]
    name = name.split(".")[0].removeprefix("go-").removesuffix("-go")
    return name.replace("-", "")


def import_aliases(root: SgNode) -> dict[str, str]:
    """Each package name a file's imports bind, with the import path it names; dot and
    blank imports bind no name."""
    found: dict[str, str] = {}
    for node in root.find_all(kind="import_spec"):
        path = node.field("path")
        if path is None or path.kind() not in STRING_KINDS:
            continue
        name = node.field("name")
        alias = name.text() if name is not None else default_alias(path.text()[1:-1])
        if alias not in HIDDEN_ALIASES:
            found[alias] = path.text()[1:-1]
    return found


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


def declares(node: SgNode) -> bool:
    """Whether a name node is one its parent declares (a function, type, parameter,
    variable, constant or field name) rather than reads."""
    parent = node.parent()
    start = node.range().start.index
    return parent is not None and any(
        name.range().start.index == start for name in parent.field_children("name")
    )


def qualified_parts(node: SgNode) -> tuple[SgNode | None, SgNode | None]:
    if node.kind() == "selector_expression":
        return node.field("operand"), node.field("field")
    return node.field("package"), node.field("name")


def references(root: SgNode) -> list[GoReference]:
    """The package-level names a file reads: pkg.Name through each imported package's
    name, and every other name it reads (not declares) in its own package."""
    aliases = import_aliases(root)
    found: list[GoReference] = []
    through: set[int] = set()
    for node in of_kinds(root, QUALIFIED_KINDS):
        package, name = qualified_parts(node)
        if package is None or name is None or package.text() not in aliases:
            continue
        through.add(package.range().start.index)
        found.append(
            GoReference(aliases[package.text()], name.text(), node.range().start.index)
        )
    found.extend(
        GoReference(None, node.text(), node.range().start.index)
        for node in of_kinds(root, LOCAL_NAMES)
        if node.range().start.index not in through and not declares(node)
    )
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


def pack() -> dict[str, Any]:
    return language_pack("go")


def build_ignored(root: SgNode) -> bool:
    """Whether a file's build constraint is the one that keeps it out of every build."""
    clause = package_clause(root).range().start.index
    ignored = pack()["build_ignored"]["constraint"]
    return any(
        node.kind() == "comment"
        and node.range().end.index <= clause
        and (constraint := BUILD.fullmatch(node.text())) is not None
        and constraint.group(1).strip() == ignored
        for node in root.children()
    )


def ignored_part(part: str) -> bool:
    ignored = pack()["build_ignored"]
    return part.startswith(tuple(ignored["prefixes"])) or part in ignored["folders"]


def in_ignored_folder(path: str) -> bool:
    """Whether a file sits in a folder the go tool ignores (testdata, vendor, or a name
    starting with . or _)."""
    return any(ignored_part(part) for part in PurePosixPath(path).parts[:-1])


def built_path(path: str) -> bool:
    """Whether the go tool reads a file at this path as package source: a Go file whose
    name does not start with an ignored prefix, outside every folder it ignores."""
    pure = PurePosixPath(path)
    return (
        pure.suffix in languages()["go"]
        and not ignored_part(pure.name)
        and not in_ignored_folder(path)
    )


def built(path: str, root: SgNode) -> bool:
    """Whether the go tool builds the file: a built path without the ignore constraint."""
    return built_path(path) and not build_ignored(root)


def test_patterns() -> list[str]:
    patterns: list[str] = pack()["test_patterns"]
    return patterns


def is_test_file(path: str) -> bool:
    """Whether go test compiles the file as a test: its name matches the pack's test
    patterns (the _test.go suffix)."""
    name = PurePosixPath(path).name
    return any(fnmatchcase(name, pattern) for pattern in test_patterns())


def test_suffixes() -> tuple[str, ...]:
    return tuple(pattern.removeprefix("*") for pattern in test_patterns())
