from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeAlias

from guardrails.commands.adapters.pytest.syntax import (
    NAMES,
    NUMBER_KINDS,
    STRING_KINDS,
    Form,
    Forms,
    Role,
    contents,
    number_value,
    piece_text,
    prefix,
    string_parts,
)
from guardrails.foundation.source.python import literal_value
from guardrails.foundation.source.tree import SourceError, named

if TYPE_CHECKING:
    from ast_grep_py import SgNode

PARAMETRIZE = "pytest.mark.parametrize"
CONSTANT_KINDS: dict[str, object] = {
    "true": True,
    "false": False,
    "none": None,
    "ellipsis": ...,
}
SEQUENCE_KINDS = frozenset({"list", "tuple"})
FOLDED_KINDS = frozenset({"call", *STRING_KINDS})
SIGNS = frozenset({"+", "-"})


@dataclass(frozen=True)
class Scope:
    aliases: Mapping[str, str]
    constants: Mapping[str, Static]


@dataclass(frozen=True)
class Static:
    node: SgNode
    scope: Scope


@dataclass(frozen=True)
class Value:
    value: int | str


@dataclass(frozen=True)
class Items:
    values: tuple[Expr, ...]


Expr: TypeAlias = "Static | Value | Items"
Row: TypeAlias = tuple[str, dict[str, Form]]
Dimension: TypeAlias = list[Row]


def evaluate(text: str) -> object:
    try:
        return literal_value(text)
    except SourceError as error:
        raise ValueError(f"not a literal: {text}") from error


def child(expr: Static, field: str) -> Static | None:
    node = expr.node.field(field)
    return None if node is None else Static(node, expr.scope)


def identifier(expr: Static) -> str | None:
    if expr.node.kind() != "identifier":
        return None
    text = expr.node.text()
    return expr.scope.aliases.get(text, text)


def resolve(expr: Expr) -> Expr:
    while isinstance(expr, Static):
        kind = expr.node.kind()
        name = identifier(expr)
        inner = named(expr.node) if kind == "parenthesized_expression" else []
        if len(inner) == 1:
            expr = Static(inner[0], expr.scope)
        elif name is not None and name in expr.scope.constants:
            expr = expr.scope.constants[name]
        else:
            return fold(expr) or expr
    return expr


def fold(expr: Static) -> Expr | None:
    kind = expr.node.kind()
    if kind == "call":
        return fold_call(expr)
    if kind in STRING_KINDS:
        return fold_string(expr)
    return None


def constant(expr: Expr) -> tuple[bool, object]:
    resolved = resolve(expr)
    if isinstance(resolved, Value):
        return True, resolved.value
    if not isinstance(resolved, Static):
        return False, None
    node = resolved.node
    kind = node.kind()
    if kind in CONSTANT_KINDS:
        return True, CONSTANT_KINDS[kind]
    if kind in NUMBER_KINDS:
        return True, number_value(node.text())
    if kind in STRING_KINDS and not formatted(node):
        return True, evaluate(node.text())
    return False, None


def formatted(node: SgNode) -> bool:
    return any("f" in prefix(part) for part in string_parts(node))


def items(expr: Expr) -> list[Expr] | None:
    resolved = resolve(expr)
    if isinstance(resolved, Items):
        return list(resolved.values)
    if isinstance(resolved, Static) and resolved.node.kind() in SEQUENCE_KINDS:
        return [Static(node, resolved.scope) for node in named(resolved.node)]
    return None


def arguments(call: Static) -> tuple[list[Expr], dict[str | None, Expr]]:
    node = call.node.field("arguments")
    positional: list[Expr] = []
    keywords: dict[str | None, Expr] = {}
    if node is not None and node.kind() == "generator_expression":
        return [Static(node, call.scope)], keywords
    for item in named(node) if node is not None else []:
        if item.kind() == "keyword_argument":
            name, value = item.field("name"), item.field("value")
            if name is not None and value is not None:
                keywords[name.text()] = Static(value, call.scope)
        elif item.kind() == "dictionary_splat":
            keywords[None] = Static(item, call.scope)
        else:
            positional.append(Static(item, call.scope))
    return positional, keywords


def qualified(expr: Expr | None) -> str:
    resolved = None if expr is None else resolve(expr)
    if not isinstance(resolved, Static):
        return ""
    name = identifier(resolved)
    if name is not None:
        return name
    attribute = resolved.node.field("attribute")
    if resolved.node.kind() != "attribute" or attribute is None:
        return ""
    return f"{qualified(child(resolved, 'object'))}.{attribute.text()}"


def fold_call(call: Static) -> Expr | None:
    positional, keywords = arguments(call)
    function = child(call, "function")
    resolved = None if function is None else resolve(function)
    if keywords or not isinstance(resolved, Static):
        return None
    name = identifier(resolved)
    if name == "len" and len(positional) == 1:
        return fold_len(positional[0])
    if name == "range" and 1 <= len(positional) <= 3:
        return fold_range(positional)
    if resolved.node.kind() == "attribute" and len(positional) == 2:
        return fold_replace(resolved, positional)
    return None


def fold_len(argument: Expr) -> Value | None:
    found, value = constant(argument)
    if found and isinstance(value, (str, bytes)):
        return Value(len(value))
    sequence = items(argument)
    return None if sequence is None else Value(len(sequence))


def fold_range(values: list[Expr]) -> Items | None:
    numbers: list[int] = []
    for value in values:
        found, number = constant(value)
        if not found or not isinstance(number, int) or isinstance(number, bool):
            return None
        numbers.append(number)
    try:
        produced = range(*numbers)
    except ValueError:
        return None
    return Items(tuple(Value(number) for number in produced))


def fold_replace(function: Static, values: list[Expr]) -> Value | None:
    attribute = function.node.field("attribute")
    target = child(function, "object")
    if attribute is None or attribute.text() != "replace" or target is None:
        return None
    texts = [constant(item) for item in (target, *values)]
    if not all(found and isinstance(text, str) for found, text in texts):
        return None
    text, old, new = (str(text) for _, text in texts)
    return Value(text.replace(old, new))


def fold_string(expr: Static) -> Value | None:
    if not formatted(expr.node):
        return None
    texts = []
    for part in string_parts(expr.node):
        text = part_text(part, expr.scope)
        if text is None:
            return None
        texts.append(text)
    return Value("".join(texts))


def part_text(part: SgNode, scope: Scope) -> str | None:
    start = prefix(part)
    if "f" not in start:
        value = evaluate(part.text())
        return value if isinstance(value, str) else None
    pieces = []
    for item in contents(part):
        if item.kind() != "interpolation":
            pieces.append(piece_text(item, start))
            continue
        found, value = constant(Static(named(item)[0], scope))
        if not found:
            return None
        pieces.append(str(value))
    return "".join(pieces)


def malformed(expr: Static) -> ValueError:
    return ValueError(f"not a static literal: {' '.join(expr.node.text().split())}")


def number(expr: Expr) -> int | float | complex:
    found, value = constant(expr)
    if (
        not found
        or isinstance(value, bool)
        or not isinstance(value, (int, float, complex))
    ):
        raise ValueError("not a static number")
    return value


def signed(expr: Static) -> object:
    operator, operand = expr.node.field("operator"), child(expr, "argument")
    if operator is None or operator.text() not in SIGNS or operand is None:
        raise malformed(expr)
    value = number(operand)
    return -value if operator.text() == "-" else +value


def complex_sum(expr: Static) -> object:
    operator = expr.node.field("operator")
    left, right = child(expr, "left"), child(expr, "right")
    if (
        operator is None
        or operator.text() not in SIGNS
        or left is None
        or right is None
    ):
        raise malformed(expr)
    real, imaginary = literal(left), number(right)
    if isinstance(real, bool) or not isinstance(real, (int, float)):
        raise malformed(expr)
    if not isinstance(imaginary, complex):
        raise malformed(expr)
    return real + imaginary if operator.text() == "+" else real - imaginary


def dictionary(expr: Static) -> object:
    found = {}
    for node in named(expr.node):
        pair = Static(node, expr.scope)
        key, value = child(pair, "key"), child(pair, "value")
        if node.kind() != "pair" or key is None or value is None:
            raise malformed(expr)
        found[literal(key)] = literal(value)
    return found


def empty_set(expr: Static) -> object:
    positional, keywords = arguments(expr)
    if qualified(child(expr, "function")) != "set" or positional or keywords:
        raise malformed(expr)
    return set()


def container(
    kind: type[list[object]] | type[tuple[object, ...]] | type[set[object]],
) -> Callable[[Static], object]:
    def read(expr: Static) -> object:
        return kind(literal(Static(node, expr.scope)) for node in named(expr.node))

    return read


LITERALS: dict[str, Callable[[Static], object]] = {
    "list": container(list),
    "tuple": container(tuple),
    "set": container(set),
    "dictionary": dictionary,
    "unary_operator": signed,
    "binary_operator": complex_sum,
    "call": empty_set,
}


def literal(expr: Expr) -> object:
    found, value = constant(expr)
    if found:
        return value
    resolved = resolve(expr)
    if isinstance(resolved, Items):
        return [literal(item) for item in resolved.values]
    if not isinstance(resolved, Static):
        raise ValueError("not a static literal")
    reader = LITERALS.get(resolved.node.kind())
    if reader is None:
        raise malformed(resolved)
    return reader(resolved)


def value_form(value: int | str) -> Form:
    if isinstance(value, str):
        return ("string", "", value)
    return ("number", repr(value))


def form_of(expr: Expr) -> Form:
    if isinstance(expr, Value):
        return value_form(expr.value)
    if isinstance(expr, Items):
        return ("list", *(form_of(item) for item in expr.values))
    return StaticForms(expr.scope).form(expr.node)


class StaticForms(Forms):
    def __init__(self, scope: Scope) -> None:
        self.scope = scope

    def name(self, node: SgNode, role: Role) -> Form:
        if role not in NAMES:
            return super().name(node, role)
        resolved = resolve(Static(node, self.scope))
        name = identifier(resolved) if isinstance(resolved, Static) else None
        return ("name", name) if name is not None else form_of(resolved)

    def form(self, node: SgNode, role: Role = Role.LOAD) -> Form:
        folded = fold(Static(node, self.scope)) if node.kind() in FOLDED_KINDS else None
        return form_of(folded) if folded is not None else super().form(node, role)


def escaped(value: object) -> str:
    return str(value).encode("unicode_escape").decode("ascii")


def parameter_names(expr: Expr) -> list[str]:
    names = literal(expr)
    if isinstance(names, str):
        return [part.strip() for part in names.split(",")]
    if isinstance(names, (tuple, list)) and all(
        isinstance(name, str) for name in names
    ):
        return [str(name) for name in names]
    raise ValueError("parametrize names must be static strings")


def literal_id(expr: Expr, name: str, index: int) -> str:
    try:
        value = literal(expr)
    except (ValueError, TypeError):
        return f"{name}{index}"
    if value is None or isinstance(value, (str, int, float, bool, complex)):
        return escaped(value)
    return f"{name}{index}"


def row_id(
    names: Sequence[str], values: Sequence[Expr], index: int, explicit: Expr | None
) -> str:
    label = literal(explicit) if explicit is not None else None
    if label is not None:
        return escaped(label)
    return "-".join(
        literal_id(value, name, index)
        for name, value in zip(names, values, strict=True)
    )


def row_values(row: Expr, width: int) -> tuple[list[Expr], Expr | None]:
    resolved = resolve(row)
    if (
        isinstance(resolved, Static)
        and resolved.node.kind() == "call"
        and qualified(child(resolved, "function")).endswith(".param")
    ):
        values, keywords = arguments(resolved)
        explicit = keywords.get("id")
    else:
        sequence = items(resolved) if width > 1 else None
        values, explicit = (sequence if sequence is not None else [resolved]), None
    if len(values) != width:
        raise ValueError("parametrize row width does not match names")
    return values, explicit


def unique_ids(rows: Dimension) -> Dimension:
    counts: dict[str, int] = {}
    for label, _ in rows:
        counts[label] = counts.get(label, 0) + 1
    offsets: dict[str, int] = {}
    unique = []
    occupied = set(counts)
    for label, bindings in rows:
        if counts[label] == 1:
            unique.append((label, bindings))
            continue
        offset = offsets.get(label, 0)
        separator = "_" if label[-1:].isdigit() else ""
        candidate = f"{label}{separator}{offset}"
        while candidate in occupied:
            offset += 1
            candidate = f"{label}{separator}{offset}"
        offsets[label] = offset + 1
        occupied.add(candidate)
        unique.append((candidate, bindings))
    return unique


def parameter_rows(call: Static) -> Dimension:
    positional, keywords = arguments(call)
    if len(positional) < 2:
        raise ValueError("parametrize requires static names and rows")
    names = parameter_names(positional[0])
    rows = items(positional[1])
    if rows is None:
        raise ValueError("parametrize rows must be a static list or tuple")
    ids = keywords.get("ids")
    labels = None if ids is None else items(ids)
    if ids is not None and labels is None:
        raise ValueError("parametrize ids must be a static list or tuple")
    if labels is not None and len(labels) != len(rows):
        raise ValueError("parametrize ids must match the number of rows")
    result: Dimension = []
    for index, row in enumerate(rows):
        values, explicit = row_values(row, len(names))
        if explicit is None and labels is not None:
            explicit = labels[index]
        result.append((
            row_id(names, values, index, explicit),
            {name: form_of(value) for name, value in zip(names, values, strict=True)},
        ))
    return unique_ids(result)


def dimensions(decorators: Sequence[SgNode], scope: Scope) -> list[Dimension]:
    found = []
    for decorator in reversed(decorators):
        call = resolve(Static(decorator, scope))
        if (
            isinstance(call, Static)
            and call.node.kind() == "call"
            and qualified(child(call, "function")) == PARAMETRIZE
        ):
            found.append(parameter_rows(call))
    return found
