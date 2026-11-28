from __future__ import annotations

import codecs
from collections.abc import Iterator, Sequence
from enum import Enum, auto
from typing import TYPE_CHECKING, TypeAlias

from guardrails.foundation.source.python import from_import, imported_name, text_literal
from guardrails.foundation.source.tree import named, of_kinds

if TYPE_CHECKING:
    from ast_grep_py import SgNode


class Role(Enum):
    LOAD = auto()
    STORE = auto()
    DELETE = auto()
    ARGUMENT = auto()
    FUNCTION = auto()
    CLASS = auto()
    ATTRIBUTE = auto()
    KEYWORD = auto()
    IMPORT = auto()
    BOUND = auto()
    ANNOTATION = auto()


Form: TypeAlias = tuple["Form | str", ...]
Part: TypeAlias = tuple["SgNode", "Role | None"]
Imported: TypeAlias = tuple[str, str, str]

NAMES = frozenset({Role.LOAD, Role.STORE, Role.DELETE})
RENAMED = frozenset({Role.ARGUMENT, Role.FUNCTION})
INERT = frozenset({Role.IMPORT, Role.BOUND})
IMPORT_KINDS = frozenset({
    "import_statement",
    "import_from_statement",
    "future_import_statement",
})
STRING_KINDS = frozenset({"string", "concatenated_string"})
NUMBER_KINDS = frozenset({"integer", "float"})
SKIPPED = frozenset({"comment", "line_continuation"})
EXCEPT_KINDS = frozenset({"except_clause", "except_group_clause"})
TARGET_KINDS = frozenset({
    "pattern_list",
    "tuple_pattern",
    "list_pattern",
    "tuple",
    "list",
    "expression_list",
    "list_splat_pattern",
    "dictionary_splat_pattern",
    "list_splat",
    "parenthesized_expression",
    "as_pattern_target",
})
KIND_ROLES = {
    **dict.fromkeys(IMPORT_KINDS, Role.IMPORT),
    "global_statement": Role.BOUND,
    "nonlocal_statement": Role.BOUND,
    "delete_statement": Role.DELETE,
    "parameters": Role.ARGUMENT,
    "lambda_parameters": Role.ARGUMENT,
    "typed_parameter": Role.ARGUMENT,
}
FIELD_ROLES = {
    "assignment": {"left": Role.STORE},
    "augmented_assignment": {"left": Role.STORE},
    "for_statement": {"left": Role.STORE},
    "for_in_clause": {"left": Role.STORE},
    "named_expression": {"name": Role.STORE},
    "as_pattern": {"alias": Role.STORE},
    "attribute": {"attribute": Role.ATTRIBUTE},
    "keyword_argument": {"name": Role.KEYWORD},
    "function_definition": {"name": Role.FUNCTION},
    "class_definition": {"name": Role.CLASS},
    "default_parameter": {"name": Role.ARGUMENT},
    "typed_default_parameter": {"name": Role.ARGUMENT, "type": Role.ANNOTATION},
    "typed_parameter": {"type": Role.ANNOTATION},
}
EXCEPT_ROLES = {"alias": Role.BOUND}
PUNCTUATION = frozenset({"(", ")", "[", "]", "{", "}", ",", ";"})
GROUPS = {"call": "argument_list", "subscript": "tuple"}
KIND_ALIASES = {
    "expression_list": "tuple",
    "pattern_list": "tuple",
    "tuple_pattern": "tuple",
    "list_pattern": "list",
    "list_splat_pattern": "list_splat",
    "dictionary_splat_pattern": "dictionary_splat",
}


def inherited_role(kind: str, role: Role) -> Role:
    if role in INERT or kind in TARGET_KINDS:
        return role
    return KIND_ROLES.get(kind, Role.LOAD)


def field_roles(node: SgNode, kind: str) -> dict[SgNode, Role]:
    roles = FIELD_ROLES.get(kind, {})
    parent = node.parent() if kind == "as_pattern" else None
    if parent is not None and parent.kind() in EXCEPT_KINDS:
        roles = EXCEPT_ROLES
    return {
        child: role
        for name, role in roles.items()
        if (child := node.field(name)) is not None
    }


def parts(node: SgNode, role: Role) -> list[Part]:
    kind = node.kind()
    default = inherited_role(kind, role)
    fields = {} if role in INERT else field_roles(node, kind)
    return [
        (child, fields.get(child, default) if child.is_named() else None)
        for child in node.children()
        if child.kind() not in SKIPPED
    ]


def named_parts(items: list[Part]) -> list[tuple[SgNode, Role]]:
    return [(child, role) for child, role in items if role is not None]


def walk(node: SgNode, role: Role = Role.LOAD) -> Iterator[tuple[SgNode, Role]]:
    pending = [(node, role)]
    while pending:
        current, current_role = pending.pop()
        yield current, current_role
        pending.extend(reversed(named_parts(parts(current, current_role))))


def string_parts(node: SgNode) -> list[SgNode]:
    return named(node) if node.kind() == "concatenated_string" else [node]


def prefix(part: SgNode) -> str:
    return named(part)[0].text().rstrip("'\"").lower()


def contents(part: SgNode) -> list[SgNode]:
    return named(part)[1:-1]


def decoded(text: str, start: str) -> str:
    if "\\" not in text or "r" in start:
        return text
    escaped = text.encode("latin-1", "backslashreplace")
    try:
        if "b" in start:
            return codecs.escape_decode(escaped)[0].decode("latin-1")
        return escaped.decode("unicode_escape")
    except UnicodeDecodeError:
        return text


def piece_text(piece: SgNode, start: str) -> str:
    text = piece.text()
    if "f" in start:
        text = text.replace("{{", "{").replace("}}", "}")
    return decoded(text, start)


def string_value(node: SgNode) -> str | None:
    if node.kind() not in STRING_KINDS or not text_literal(node):
        return None
    return "".join(
        piece_text(child, prefix(part))
        for part in string_parts(node)
        for child in contents(part)
    )


def string_texts(node: SgNode) -> list[str]:
    pieces = string_parts(node)
    if any("b" in prefix(part) for part in pieces):
        return []
    texts = [""]
    for part in pieces:
        start = prefix(part)
        for child in contents(part):
            if child.kind() == "interpolation":
                texts.append("")
            else:
                texts[-1] += piece_text(child, start)
    return [text for text in texts if text]


def format_texts(node: SgNode) -> list[str]:
    start = node.range().start.index
    text = node.text()
    texts, cursor = [], 1
    for item in named(node):
        if item.kind() == "format_expression":
            texts.append(text[cursor : item.range().start.index - start])
            cursor = item.range().end.index - start
    texts.append(text[cursor:])
    return [item for item in texts if item]


def plain_import(item: SgNode) -> Imported:
    name = imported_name(item)
    alias = item.field("alias") if item.kind() == "aliased_import" else None
    if alias is not None:
        return alias.text(), name, name
    top = name.split(".")[0]
    return top, top, name


def from_names(statement: SgNode) -> list[tuple[str, str]]:
    if any(item.kind() == "wildcard_import" for item in named(statement)):
        return [("*", "*")]
    found = []
    for item in statement.field_children("name"):
        alias = item.field("alias") if item.kind() == "aliased_import" else None
        name = imported_name(item)
        found.append((name if alias is None else alias.text(), name))
    return found


def import_aliases(statement: SgNode) -> list[Imported]:
    if statement.kind() == "import_statement":
        return [plain_import(item) for item in statement.field_children("name")]
    source = from_import(statement)
    module = "." * source.level + source.module + ("." if source.module else "")
    return [
        (local, module + original, original)
        for local, original in from_names(statement)
    ]


def number_value(text: str) -> int | float | complex:
    lowered = text.lower().replace("_", "")
    if lowered.endswith("j"):
        return complex(lowered)
    if lowered.startswith(("0x", "0o", "0b")) or not {".", "e"} & set(lowered):
        return int(lowered, 0)
    return float(lowered)


def number_form(text: str) -> Form:
    try:
        return ("number", repr(number_value(text)))
    except ValueError:
        return ("number", text)


def add_text(pieces: list[Form | str], text: str) -> None:
    if pieces and isinstance(pieces[-1], str):
        pieces[-1] += text
    else:
        pieces.append(text)


def ungrouped(kind: str, inner: Sequence[tuple[SgNode, Role]]) -> bool:
    if kind == "call":
        return len(inner) == 2 and inner[1][0].kind() == "generator_expression"
    return kind == "subscript" and len(inner) > 2


class Forms:
    def name(self, node: SgNode, role: Role) -> Form:
        text = node.text()
        if role in NAMES:
            return self.variable(text, role)
        if role in RENAMED:
            return (role.name.lower(), self.renamed(text))
        return ("raw", text)

    def variable(self, text: str, role: Role) -> Form:
        return ("name", text)

    def renamed(self, text: str) -> str:
        return text

    def form(self, node: SgNode, role: Role = Role.LOAD) -> Form:
        kind = node.kind()
        if kind == "identifier":
            return self.name(node, role)
        if kind in STRING_KINDS:
            return self.string(node)
        if kind in NUMBER_KINDS:
            return number_form(node.text())
        return self.compound(node, kind, role)

    def compound(self, node: SgNode, kind: str, role: Role) -> Form:
        items = parts(node, role)
        inner = named_parts(items)
        if kind == "parenthesized_expression" and len(inner) == 1:
            return self.form(*inner[0])
        if ungrouped(kind, inner):
            return (kind, self.form(*inner[0]), (GROUPS[kind], *self.pieces(inner[1:])))
        if not items:
            return (kind, node.text())
        return (KIND_ALIASES.get(kind, kind), *self.pieces(items))

    def pieces(self, items: Sequence[Part]) -> list[Form | str]:
        found: list[Form | str] = []
        for child, role in items:
            if role is None:
                text = " ".join(child.text().split())
                if text not in PUNCTUATION:
                    found.append(text)
            elif role is not Role.ANNOTATION:
                found.append(self.form(child, role))
        return found

    def string(self, node: SgNode) -> Form:
        flags: set[str] = set()
        pieces: list[Form | str] = []
        for part in string_parts(node):
            start = prefix(part)
            flags.update(start)
            for child in contents(part):
                if child.kind() == "interpolation":
                    pieces.append(self.form(child))
                else:
                    add_text(pieces, piece_text(child, start))
        return ("string", "".join(sorted(flags - {"r", "u"})), *pieces)


def import_map(root: SgNode) -> dict[str, str]:
    return {
        local: target
        for statement in of_kinds(root, IMPORT_KINDS)
        for local, target, _ in import_aliases(statement)
        if not target.startswith(".")
    }
