from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from guardrails.foundation.source import go, python, typescript
from guardrails.foundation.source.tree import (
    Definition,
    Span,
    parse,
    source_language,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\S")
LINE_ENDINGS = frozenset(b"\r\n")
SPACE = ord(" ")

Gram = tuple[str, str, str]
DefinitionReader = Callable[["SgNode", str], list[Definition]]
SpanReader = Callable[["SgNode", str], list[Span]]

DEFINITIONS: dict[str, DefinitionReader] = {
    "python": python.definitions,
    "typescript": typescript.definitions,
    "go": go.definitions,
}
COMMENTS: dict[str, SpanReader] = {
    "python": python.comment_spans,
    "typescript": typescript.comment_spans,
    "go": go.comment_spans,
}


def removed_spans(language: str, root: SgNode, source: str) -> list[Span]:
    found = COMMENTS[language](root, source)
    if language == "python":
        found.extend(python.docstring_spans(root, source))
    return found


def cleaned(source: str, removed: Iterable[Span]) -> bytes:
    data = bytearray(source.encode())
    for start, end in removed:
        data[start:end] = bytes(
            byte if byte in LINE_ENDINGS else SPACE for byte in data[start:end]
        )
    return bytes(data)


def segment(text: bytes, definition: Definition) -> str:
    start, end = definition.span
    return text[start:end].decode(errors="replace")


def grams(text: str) -> frozenset[Gram]:
    tokens = TOKEN.findall(text)
    return frozenset(zip(tokens, tokens[1:], tokens[2:], strict=False))


def definition_grams(
    path: str, source: str
) -> list[tuple[Definition, frozenset[Gram]]]:
    language = source_language(path)
    root = parse(path, source)
    text = cleaned(source, removed_spans(language, root, source))
    return [
        (item, grams(segment(text, item)))
        for item in DEFINITIONS[language](root, source)
    ]
