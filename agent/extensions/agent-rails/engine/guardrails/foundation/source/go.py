from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.foundation.source.tree import (
    Declaration,
    Definition,
    Offsets,
    SourceError,
    definition,
    language_pack,
    languages,
    line,
    named,
    of_kinds,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

TEST_NAME = re.compile(r"^(Test|Fuzz|Example)(?![a-z])\w*$")
EMBED = re.compile(r"//go:embed\s(.*)")
BUILD = re.compile(r"//go:build\s(.*)")
INTERPRETED = re.compile(r'"((?:[^"\\\n]|\\.)*)"', re.DOTALL)
GO_ESCAPE = re.compile(
    r"\\(?:([abfnrtv\\\"])|([0-7]{3})|x([0-9A-Fa-f]{2})|u([0-9A-Fa-f]{4})"
    r"|U([0-9A-Fa-f]{8})|(.))",
    re.DOTALL,
)
SINGLE_ESCAPES = {
    "a": 0x07,
    "b": 0x08,
    "f": 0x0C,
    "n": 0x0A,
    "r": 0x0D,
    "t": 0x09,
    "v": 0x0B,
    "\\": 0x5C,
    '"': 0x22,
}
SURROGATES = range(0xD800, 0xE000)
LAST_CODE_POINT = 0x10FFFF
EMBED_PATTERN = re.compile(r'"(?:[^"\\]|\\.)*"|`[^`]*`|\S+')
VERSION_ELEMENT = re.compile(r"^v\d+$")
HIDDEN_ALIASES = frozenset({".", "_"})
RECEIVER_WRAPPERS = frozenset({"pointer_type", "generic_type", "parenthesized_type"})
STRING_KINDS = frozenset({"interpreted_string_literal", "raw_string_literal"})
QUALIFIED_KINDS = frozenset({"selector_expression", "qualified_type"})
LOCAL_NAMES = frozenset({"identifier", "type_identifier"})
VALUE_DECLARATIONS = {"const_declaration": "constant", "var_declaration": "variable"}


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


def literal(text: str) -> str | None:
    """The value of a Go string literal's source text, as the compiler reads it: a raw
    string verbatim without carriage returns, an interpreted string with its escapes
    decoded; None when it is no valid string literal."""
    if len(text) >= 2 and text[0] == text[-1] == "`":
        return text[1:-1].replace("\r", "")
    match = INTERPRETED.fullmatch(text)
    if match is None:
        return None
    data = bytearray()
    at = 0
    body = match.group(1)
    for escape in GO_ESCAPE.finditer(body):
        data += body[at : escape.start()].encode()
        value = escaped(escape)
        if value is None:
            return None
        data += value
        at = escape.end()
    data += body[at:].encode()
    return data.decode("utf-8", "replace")


def escaped(escape: re.Match[str]) -> bytes | None:
    single, octal, byte, short, long, other = escape.groups()
    if single is not None:
        return bytes([SINGLE_ESCAPES[single]])
    if octal is not None:
        value = int(octal, 8)
        return bytes([value]) if value <= 0xFF else None
    if byte is not None:
        return bytes([int(byte, 16)])
    if other is not None:
        return None
    point = int(short or long, 16)
    if point in SURROGATES or point > LAST_CODE_POINT:
        return None
    return chr(point).encode()


def string_value(node: SgNode | None) -> str | None:
    """The value of a string literal node, or None for any other node."""
    if node is None or node.kind() not in STRING_KINDS:
        return None
    return literal(node.text())


def import_paths(root: SgNode) -> list[tuple[SgNode, str]]:
    return [
        (node, value)
        for node in root.find_all(kind="import_spec")
        if (value := string_value(node.field("path"))) is not None
    ]


def imports(root: SgNode) -> list[GoImport]:
    return [GoImport(value, line(node)) for node, value in import_paths(root)]


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
    for node, value in import_paths(root):
        name = node.field("name")
        alias = name.text() if name is not None else default_alias(value)
        if alias not in HIDDEN_ALIASES:
            found[alias] = value
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


def type_members(spec: SgNode) -> list[tuple[str, str, SgNode]]:
    """The fields of a struct type and the methods of an interface type a type spec
    declares, each with its kind."""
    body = spec.field("type")
    kind = body.kind() if body is not None else ""
    if body is None or kind not in {"struct_type", "interface_type"}:
        return []
    if kind == "interface_type":
        return [
            (name.text(), "method", item)
            for item in named(body)
            if item.kind() == "method_elem" and (name := item.field("name")) is not None
        ]
    return [
        (name.text(), "field", field)
        for fields in named(body)
        for field in named(fields)
        if field.kind() == "field_declaration"
        for name in field.field_children("name")
    ]


def type_declarations(node: SgNode) -> list[Declaration]:
    found: list[Declaration] = []
    for spec in named(node):
        name = spec.field("name")
        if name is None:
            continue
        kind = "type_alias" if spec.kind() == "type_alias" else "type"
        found.append(Declaration(name.text(), kind, spec))
        found.extend(
            Declaration(member, member_kind, item, name.text())
            for member, member_kind, item in type_members(spec)
        )
    return found


def value_specs(node: SgNode) -> list[SgNode]:
    """The const or var specs of a declaration, grouped in parentheses or not."""
    specs = named(node)
    if len(specs) == 1 and specs[0].kind() == "var_spec_list":
        specs = named(specs[0])
    return [spec for spec in specs if spec.kind() in {"const_spec", "var_spec"}]


def declarations(root: SgNode) -> list[Declaration]:
    """The package name and every package-level declaration: functions, methods (owned
    by their receiver type), types with their struct fields and interface methods,
    constants and variables; the blank name declares nothing."""
    found = [Declaration(package_name(root), "package", package_clause(root))]
    for node in named(root):
        kind = node.kind()
        name = node.field("name")
        if kind == "function_declaration" and name is not None:
            found.append(Declaration(name.text(), "function", node))
        elif kind == "method_declaration" and name is not None:
            found.append(Declaration(name.text(), "method", node, receiver_type(node)))
        elif kind == "type_declaration":
            found.extend(type_declarations(node))
        elif kind in VALUE_DECLARATIONS:
            found.extend(
                Declaration(item.text(), VALUE_DECLARATIONS[kind], spec)
                for spec in value_specs(node)
                for item in spec.field_children("name")
                if item.text() != "_"
            )
    return found


def bound_names(root: SgNode) -> list[str]:
    return list(dict.fromkeys(item.name for item in declarations(root)))


def embed_value(token: str) -> str:
    if not token.startswith(('"', "`")):
        return token
    value = literal(token)
    if value is None:
        raise SourceError(f"invalid //go:embed pattern {token}")
    return value


def class_pattern(pattern: str, start: int) -> tuple[str, int]:
    """A path.Match character class ([a-z], [^_], [\\]]) at pattern[start] as a regular
    expression class, and the index after it; SourceError when it is malformed."""
    at, negated = start + 1, False
    ranges: list[tuple[str, str]] = []
    if pattern.startswith("^", at):
        negated, at = True, at + 1
    while not (pattern.startswith("]", at) and ranges):
        low, at = class_character(pattern, at)
        high = low
        if pattern.startswith("-", at):
            high, at = class_character(pattern, at + 1)
        ranges.append((low, high))
    body = "".join(
        f"{re.escape(low)}-{re.escape(high)}" for low, high in ranges if low <= high
    )
    if not body:
        return ("[^/]" if negated else "(?!)"), at + 1
    return f"[{'^' if negated else ''}{body}]", at + 1


def class_character(pattern: str, at: int) -> tuple[str, int]:
    if at >= len(pattern) or pattern[at] in "-]":
        raise SourceError(f"invalid //go:embed pattern {pattern}")
    if pattern[at] == "\\":
        at += 1
    if at >= len(pattern) - 1:
        raise SourceError(f"invalid //go:embed pattern {pattern}")
    return pattern[at], at + 1


def path_match(pattern: str) -> re.Pattern[str]:
    """A Go path.Match pattern as a regular expression: * and ? never match /, a class
    negates with ^, a backslash escapes the next character, and ** is two stars."""
    parts, at = [], 0
    while at < len(pattern):
        character = pattern[at]
        if character == "*":
            parts.append("[^/]*")
        elif character == "?":
            parts.append("[^/]")
        elif character == "[":
            text, at = class_pattern(pattern, at)
            parts.append(text)
            continue
        elif character == "\\":
            at += 1
            if at == len(pattern):
                raise SourceError(f"invalid //go:embed pattern {pattern}")
            parts.append(re.escape(pattern[at]))
        else:
            parts.append(re.escape(character))
        at += 1
    return re.compile("".join(parts))


def embeds(pattern: str, relative: str) -> bool:
    """Whether a //go:embed pattern embeds a file at a path relative to its package: the
    pattern's path elements match the path's first elements by path.Match, and a file
    below a matched folder is embedded unless a name below it starts with a hidden
    prefix (all: embeds those too)."""
    rules = pack()["embed"]
    walk_all = pattern.startswith(rules["all_prefix"])
    segments = pattern.removeprefix(rules["all_prefix"]).split("/")
    names = relative.split("/")
    if len(names) < len(segments) or not all(
        path_match(segment).fullmatch(name)
        for name, segment in zip(names, segments, strict=False)
    ):
        return False
    hidden = tuple(rules["hidden_prefixes"])
    return walk_all or not any(
        name.startswith(hidden) for name in names[len(segments) :]
    )


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
