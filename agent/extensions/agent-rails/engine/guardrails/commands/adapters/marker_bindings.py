"""Constant bindings a marker read may go through: names bound to a string or a dotted
name (KEY = "VITEST", env = process.env), read from the syntax tree, so text inside a
string is never a binding. inline_bindings writes each bound value in place of the
name's later reads, for the adapters' marker patterns to scan."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

from guardrails.commands.adapters.hook_vocabulary import fragment

if TYPE_CHECKING:
    from guardrails.foundation.source.tree import SgNode

STRING = r"""(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|`[^`\n]*`)"""
DOTTED = r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+"
CONSTANT = re.compile(rf"{STRING}|{DOTTED}")
GO_CONSTANT = re.compile(STRING)
ENVIRONMENT = re.compile(fragment("javascript_environment"))
PROPERTY = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*(?::\s*([A-Za-z_$][\w$]*))?\s*$")
WRITE = re.compile(r"[ \t]*(?:[-+*/%|&^]?=(?!=)|:(?!:))")
GO_SPECS = frozenset({"const_spec", "var_spec"})
GO_ASSIGNMENTS = frozenset({"short_var_declaration", "assignment_statement"})
Bound = tuple[str, str, tuple[int, int]]


def span(node: SgNode) -> tuple[int, int]:
    return node.range().start.index, node.range().end.index


def only(node: SgNode | None) -> SgNode | None:
    """The single expression of an expression list, or the node itself."""
    if node is None or node.kind() != "expression_list":
        return node
    items = [child for child in node.children() if child.is_named()]
    return items[0] if len(items) == 1 else None


def python_bindings(root: SgNode) -> Iterator[Bound]:
    for node in root.find_all(kind="assignment"):
        left, right = node.field("left"), node.field("right")
        if (
            left is not None
            and right is not None
            and left.kind() == "identifier"
            and CONSTANT.fullmatch(right.text())
        ):
            yield left.text(), right.text(), span(left)


def destructured(node: SgNode, value: SgNode) -> Iterator[Bound]:
    """const { NODE_ENV, VITEST: flag } = process.env binds NODE_ENV and flag."""
    pattern = node.field("name")
    if pattern is None or ENVIRONMENT.fullmatch(value.text()) is None:
        return
    for item in pattern.text().strip("{}").split(","):
        named = PROPERTY.match(item)
        if named:
            yield named[2] or named[1], f"{value.text()}.{named[1]}", span(pattern)


def script_bindings(root: SgNode) -> Iterator[Bound]:
    for node in root.find_all(kind="variable_declarator"):
        name, value = node.field("name"), node.field("value")
        if name is None or value is None:
            continue
        if name.kind() == "object_pattern":
            yield from destructured(node, value)
        elif name.kind() == "identifier" and CONSTANT.fullmatch(value.text()):
            yield name.text(), value.text(), span(name)


def go_bindings(root: SgNode) -> Iterator[Bound]:
    for node in root.find_all({
        "rule": {"any": [{"kind": kind} for kind in sorted(GO_SPECS | GO_ASSIGNMENTS)]}
    }):
        if node.kind() in GO_SPECS:
            names = node.field_children("name")
            name = names[0] if len(names) == 1 else None
        else:
            name = only(node.field("left"))
        value = only(
            node.field("value") if node.kind() in GO_SPECS else node.field("right")
        )
        if (
            name is not None
            and value is not None
            and name.kind() == "identifier"
            and GO_CONSTANT.fullmatch(value.text())
        ):
            yield name.text(), value.text(), span(name)


BINDERS: dict[str, Callable[[SgNode], Iterator[Bound]]] = {
    "python": python_bindings,
    "typescript": script_bindings,
    "go": go_bindings,
}


def bindings(
    root: SgNode, language: str
) -> tuple[dict[str, str], list[tuple[int, int]]]:
    """Each bound name with its value, and the spans that define them."""
    bound: dict[str, str] = {}
    spans = []
    for name, value, where in BINDERS[language](root):
        bound[name] = value
        spans.append(where)
    return bound, spans


def inline_bindings(source: str, root: SgNode, language: str) -> str:
    """The source with each read of a bound name replaced by its value; a defining
    name and a name written to stay."""
    bound, spans = bindings(root, language)
    if not bound:
        return source
    names = "|".join(sorted(map(re.escape, bound), key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w$.])({names})(?![\w$])")

    def substitute(match: re.Match[str]) -> str:
        start = match.start()
        defining = any(low <= start < high for low, high in spans)
        if defining or WRITE.match(source, match.end()):
            return match[0]
        return bound[match[1]]

    return pattern.sub(substitute, source)
