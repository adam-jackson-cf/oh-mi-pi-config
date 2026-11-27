"""What a proposer shows of one source file in any structure language: each top-level
definition's header with the first line of its doc, and the file's own doc
(structure-languages.json proposers.signals, STRUCT.kind-candidates)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from guardrails.foundation.source import comments, go, python, typescript
from guardrails.foundation.source.tree import (
    end_line,
    language_of,
    language_pack,
    line,
    named,
    structure_languages,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

RULE = "STRUCT.kind-candidates"
MAX_SIGNATURE = 300
MAX_DOC_LINE = 200
MAX_FILE_DOC = 600
DOC_COMMENT = "/**"
GO_DIRECTIVE = re.compile(r"^//(go|line):")
SCRIPT_BODIES = {
    "function_declaration": "body",
    "generator_function_declaration": "body",
    "class_declaration": "body",
    "abstract_class_declaration": "body",
    "interface_declaration": "body",
    "enum_declaration": "body",
    "type_alias_declaration": "value",
}
SCRIPT_PREAMBLE = frozenset({"comment", "hash_bang_line"})
GO_BODIES = {"function_declaration": "body", "method_declaration": "body"}


@dataclass(frozen=True)
class Entry:
    """One top-level definition: its header as written and its doc's first line."""

    signature: str
    doc: str


@dataclass(frozen=True)
class Outline:
    doc: str
    entries: tuple[Entry, ...]


def first_line(doc: str) -> str:
    for text in doc.strip().splitlines():
        if text.strip():
            return text.strip()[:MAX_DOC_LINE]
    return ""


def python_signature(node: SgNode, lines: list[str]) -> str:
    """A def or class header: its lines from the keyword up to the body's first
    statement, cut at the colon that ends the header."""
    body = node.field("body")
    statements = [] if body is None else named(body)
    start = line(node) - 1
    end = line(statements[0]) - 1 if statements else start + 1
    if statements and line(statements[0]) - 1 <= start:
        end = start + 1
    text = " ".join(text.strip() for text in lines[start : max(end, start + 1)])
    depth = 0
    for index, character in enumerate(text):
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == ":" and depth == 0 and index > 0:
            return text[:index].strip()[:MAX_SIGNATURE]
    return text[:MAX_SIGNATURE]


def python_outline(root: SgNode, source: str) -> Outline:
    """The module docstring, and each top-level def and class with its docstring's first
    line; SourceError when a docstring is no valid string literal."""
    lines = source.splitlines()
    entries = tuple(
        Entry(
            python_signature(node, lines),
            first_line(python.body_docstring(node.field("body"))),
        )
        for node in python.members(root)
    )
    return Outline(python.body_docstring(root).strip()[:MAX_FILE_DOC], entries)


def header(source: str, start: int, stop: int) -> str:
    return " ".join(source[start:stop].split()).rstrip("{= ")[:MAX_SIGNATURE]


def comment_text(node: SgNode, language: str) -> str:
    found = comments.markers(language_pack(language)["comment_syntax"])
    lines = comments.content(node.text(), found).splitlines()
    return "\n".join(text.strip().removeprefix("*").strip() for text in lines)


def doc_block(previous: list[SgNode], at: int) -> list[SgNode]:
    """The comments that end on the line directly above line at, each ending on the line
    above the next: a declaration's doc comment."""
    block: list[SgNode] = []
    for node in reversed(previous):
        if node.kind() != "comment" or end_line(node) + 1 != at:
            break
        block.insert(0, node)
        at = line(node)
    return block


def script_doc(previous: list[SgNode], statement: SgNode) -> str:
    block = doc_block(previous, line(statement))
    if not block or not block[-1].text().startswith(DOC_COMMENT):
        return ""
    return first_line(comment_text(block[-1], "typescript"))


def script_entries(statement: SgNode, source: str) -> list[str]:
    node = typescript.declaration(statement)
    if node is None:
        return []
    start = statement.range().start.index
    if node.kind() in SCRIPT_BODIES:
        body = node.field(SCRIPT_BODIES[node.kind()])
        stop = node.range().end.index if body is None else body.range().start.index
        return [header(source, start, stop)]
    if node.kind() != "lexical_declaration":
        return []
    found = []
    for declarator in named(node):
        value = declarator.field("value")
        if value is not None and value.kind() in typescript.FUNCTION_VALUES:
            body = value.field("body")
            stop = value.range().end.index if body is None else body.range().start.index
            found.append(header(source, start, stop))
    return found


def script_outline(root: SgNode, source: str) -> Outline:
    """The comments before the first statement, and each top-level function, class,
    interface, enum, type alias and function-valued const or let with the first line of
    the /** comment directly above it."""
    children = [child for child in root.children() if child.is_named()]
    preamble: list[str] = []
    for child in children:
        if child.kind() not in SCRIPT_PREAMBLE:
            break
        if child.kind() == "comment":
            preamble.append(comment_text(child, "typescript"))
    entries: list[Entry] = []
    for index, statement in enumerate(children):
        if statement.kind() in SCRIPT_PREAMBLE:
            continue
        doc = script_doc(children[:index], statement)
        entries.extend(Entry(text, doc) for text in script_entries(statement, source))
    return Outline("\n".join(preamble).strip()[:MAX_FILE_DOC], tuple(entries))


def go_doc(previous: list[SgNode], at: int) -> str:
    block = [
        node for node in doc_block(previous, at) if not GO_DIRECTIVE.match(node.text())
    ]
    return "\n".join(comment_text(node, "go") for node in block).strip()


def go_entries(node: SgNode, source: str) -> list[str]:
    start = node.range().start.index
    if node.kind() in GO_BODIES:
        body = node.field(GO_BODIES[node.kind()])
        stop = node.range().end.index if body is None else body.range().start.index
        return [header(source, start, stop)]
    if node.kind() != "type_declaration":
        return []
    return [
        header(f"type {spec.text()}".split("{")[0], 0, len(spec.text()) + 5)
        for spec in named(node)
        if spec.field("name") is not None
    ]


def go_outline(root: SgNode, source: str) -> Outline:
    """The package doc comment, and each top-level function, method and type with the
    first line of the doc comment directly above it."""
    children = [child for child in root.children() if child.is_named()]
    clause = go.package_clause(root)
    entries: list[Entry] = []
    doc = ""
    for index, node in enumerate(children):
        if node.kind() == "comment":
            continue
        above = go_doc(children[:index], line(node))
        if node.range().start.index == clause.range().start.index:
            doc = above
        entries.extend(
            Entry(text, first_line(above)) for text in go_entries(node, source)
        )
    return Outline(doc[:MAX_FILE_DOC], tuple(entries))


def outline(path: str, root: SgNode, source: str) -> Outline:
    """A parsed file's outline in its language; SourceError when it cannot be read."""
    language = language_of(path)
    if language == "python":
        return python_outline(root, source)
    if language == "typescript":
        return script_outline(root, source)
    return go_outline(root, source)


def folder_entry(path: str) -> bool:
    """Whether a file stands for its folder: Python's package marker, a TypeScript index
    file; no Go file does (proposers.signals folder_entry)."""
    language = language_of(path)
    name = PurePosixPath(path).name
    if language == "python":
        marker: str = language_pack("python")["package_marker"]
        return name == marker
    signals = structure_languages()["proposers"]["signals"][RULE][language]
    stem: str | None = signals["folder_entry_stem"]
    return stem is not None and name.split(".")[0] == stem
