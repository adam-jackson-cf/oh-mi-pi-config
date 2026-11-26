from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from itertools import accumulate
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ast_grep_py import Config as Config, SgNode as SgNode

DATA_FOLDER = Path(__file__).resolve().parents[3] / "data"
GRAMMARS = {
    ".tsx": "tsx",
    ".jsx": "tsx",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
}
EMPTY_TOKENS = frozenset({"raw_string_literal_content"})
KEYWORD_ARGUMENTS = frozenset({"keyword_argument"})

Span = tuple[int, int]


class SourceError(ValueError):
    pass


@dataclass(frozen=True)
class Definition:
    name: str
    line: int
    end_line: int
    span: Span


@cache
def structure_languages() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (DATA_FOLDER / "structure-languages.json").read_bytes()
    )
    return data


def languages() -> dict[str, tuple[str, ...]]:
    return {
        name: tuple(entry["extensions"])
        for name, entry in structure_languages()["languages"].items()
    }


def not_applicable(rule_id: str) -> dict[str, dict[str, str]]:
    """The languages a structure rule does not apply to, each with its reason."""
    found: dict[str, dict[str, str]] = structure_languages()["not_applicable"].get(
        rule_id, {}
    )
    return found


def rule_definitions(rule_id: str) -> dict[str, dict[str, Any]]:
    data = structure_languages()
    rule = data["rules"][rule_id]
    return {
        language: rule[language] for language in data["languages"] if language in rule
    }


def language_of(path: str) -> str | None:
    suffix = PurePosixPath(path).suffix
    return next(
        (name for name, suffixes in languages().items() if suffix in suffixes), None
    )


def source_language(path: str) -> str:
    language = language_of(path)
    if language is None:
        raise SourceError(f"{path} is not Python, TypeScript or Go source")
    return language


def grammar(path: str) -> str:
    return GRAMMARS.get(PurePosixPath(path).suffix, source_language(path))


def missing(node: SgNode) -> bool:
    return (
        node.parent() is not None and node.is_leaf() and node.kind() not in EMPTY_TOKENS
    )


def tolerant_tree(source: str, language: str) -> SgNode:
    """The syntax tree of source in a grammar, keeping error nodes, for pattern scans of
    text that need not parse (such as source with bindings inlined)."""
    from ast_grep_py import SgRoot

    return SgRoot(source, language).root()


def parse(path: str, source: str) -> SgNode:
    language = grammar(path)
    root = tolerant_tree(source, language)
    broken = (
        root.find(kind="ERROR")
        or next((node for node in root.find_all(regex="^$") if missing(node)), None)
        or (argument_order_error(root) if language == "python" else None)
    )
    if broken is not None:
        raise SourceError(
            f"cannot parse {path} as {language}: line {line(broken)} is not valid"
        )
    return root


def argument_order_error(root: SgNode) -> SgNode | None:
    for arguments in root.find_all(kind="argument_list"):
        keyword = double = False
        for item in named(arguments):
            kind = item.kind()
            if kind in KEYWORD_ARGUMENTS:
                keyword = True
            elif kind == "dictionary_splat":
                double = True
            elif kind == "list_splat":
                if double:
                    return item
            elif keyword or double:
                return item
    return None


def line(node: SgNode) -> int:
    return node.range().start.line + 1


def end_line(node: SgNode) -> int:
    return node.range().end.line + 1


def named(node: SgNode) -> list[SgNode]:
    return [
        child
        for child in node.children()
        if child.is_named() and child.kind() != "comment"
    ]


def of_kinds(root: SgNode, kinds: frozenset[str]) -> list[SgNode]:
    return root.find_all({"rule": {"any": [{"kind": kind} for kind in sorted(kinds)]}})


def unique_texts(root: SgNode, kinds: frozenset[str]) -> list[str]:
    return list(dict.fromkeys(node.text() for node in of_kinds(root, kinds)))


class Offsets:
    def __init__(self, source: str) -> None:
        self.table = (
            None
            if source.isascii()
            else [0, *accumulate(len(char.encode()) for char in source)]
        )

    def byte(self, index: int) -> int:
        return index if self.table is None else self.table[index]

    def span(self, first: SgNode, last: SgNode | None = None) -> Span:
        end = (first if last is None else last).range().end.index
        return self.byte(first.range().start.index), self.byte(end)


def spans(source: str, nodes: list[SgNode]) -> list[Span]:
    offsets = Offsets(source)
    return [offsets.span(node) for node in nodes]


def definition(name: str, offsets: Offsets, first: SgNode, last: SgNode) -> Definition:
    return Definition(name, line(first), end_line(last), offsets.span(first, last))
