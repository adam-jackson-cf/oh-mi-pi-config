"""The comments of a source file: a syntax tree's comment nodes for Python, TypeScript
and Go, and for every other file the markers its convention pack's comment_syntax names
(conventions.comment_syntax), read past string literals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from guardrails.foundation.source.tree import comment_nodes, end_line, line

if TYPE_CHECKING:
    from ast_grep_py import SgNode

LINE_END = "\n"

Marker = tuple[str, str]


@dataclass(frozen=True)
class Comment:
    """One comment: its first and last line and its text without the markers."""

    line: int
    end_line: int
    text: str


def markers(syntax: dict[str, Any]) -> list[Marker]:
    """A syntax's comment markers as (start, end) pairs, a line comment ending at the line
    end; longer starts first, so Lua's --[[ opens before --."""
    found = [(start, LINE_END) for start in syntax.get("line", [])]
    found.extend((start, end) for start, end in syntax.get("block", []))
    return sorted(found, key=lambda marker: -len(marker[0]))


def content(text: str, found: list[Marker]) -> str:
    """A comment's text without the markers that open and close it."""
    for start, end in found:
        if text.startswith(start):
            body = text[len(start) :]
            if end != LINE_END and body.endswith(end):
                body = body[: -len(end)]
            return body
    return text


def tree_comments(root: SgNode, syntax: dict[str, Any]) -> list[Comment]:
    found = markers(syntax)
    return [
        Comment(line(node), end_line(node), content(node.text(), found))
        for node in comment_nodes(root)
    ]


def opens(text: str, at: int, start: str, delimiters: str | None) -> bool:
    return text.startswith(start, at) and not (
        delimiters is not None and at > 0 and text[at - 1] not in delimiters
    )


def comment_stop(text: str, at: int, marker: Marker) -> int:
    start, end = marker
    stop = text.find(end, at + len(start))
    return len(text) if stop < 0 else stop + (0 if end == LINE_END else len(end))


def string_stop(text: str, at: int) -> int:
    """The end of the string literal opening at text[at]: a triple or single quote,
    backslash escapes, and a doubled single quote kept inside (SQL's 'it''s')."""
    quote = text[at] * (3 if text.startswith(text[at] * 3, at) else 1)
    stop = at + len(quote)
    while stop < len(text):
        if text[stop] == "\\":
            stop += 2
        elif text.startswith(quote, stop):
            stop += len(quote)
            if len(quote) == 1 and text.startswith(quote, stop):
                stop += 1
                continue
            break
        else:
            stop += 1
    return stop


def lexed(text: str, syntax: dict[str, Any]) -> list[Comment]:
    """The comments of a file no grammar reads, by its comment syntax: a marker opens a
    comment unless it sits inside a string literal of one of the syntax's quotes."""
    found = markers(syntax)
    delimiters = syntax.get("word_delimiters")
    quotes = tuple(syntax.get("quotes", []))
    comments: list[Comment] = []
    at = 0
    while at < len(text):
        marker = next(
            (item for item in found if opens(text, at, item[0], delimiters)), None
        )
        if marker is not None:
            stop = comment_stop(text, at, marker)
            body = text[at:stop]
            comments.append(
                Comment(
                    text.count(LINE_END, 0, at) + 1,
                    text.count(LINE_END, 0, max(at, stop - 1)) + 1,
                    content(body, [marker]),
                )
            )
        elif text.startswith(quotes, at):
            stop = string_stop(text, at)
        else:
            at += 1
            continue
        at = stop
    return comments
