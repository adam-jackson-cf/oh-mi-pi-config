from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.structure_naming import (
    responsibility_overlap as overlap,
    tokens,
)
from guardrails.foundation.source import python
from guardrails.foundation.source.tree import Definition, parse

pytestmark = pytest.mark.fast

ENGINE = Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine"

DECORATED = '''"""Billing reports."""

import functools


@functools.cache
@staticmethod
def summarise_orders(orders, customer):
    """Totals per customer.

    Multi-line # not a comment.
    """
    totals = {}  # running totals
    for order in orders:
        if order.customer == customer:
            totals[order.sku] = totals.get(order.sku, 0) + order.amount
    return sorted(totals.items(), key=lambda item: item[1])


async def fetch_rows(client, query):
    rows = []
    async for row in client.stream(query):
        rows.append(row)
    return [row for row in rows if row]
'''

CLASSES = '''class Ledger:
    # leading comment
    """Ledger of entries."""

    kind = "ledger"

    def __init__(self, entries):
        self.entries = list(entries)
        self.index = {}
        for entry in self.entries:
            self.index[entry.key] = entry

    @property
    def balance(self):
        def signed(entry):
            """Nested docstring."""
            return entry.amount if entry.credit else -entry.amount
        total = 0
        for entry in self.entries:
            total += signed(entry)
        return total

    class Entry:
        def describe(self):
            return f"{self.key}: {self.amount} # inside f-string"

    @classmethod
    async def load(cls, store):
        rows = await store.rows()
        result = cls(rows)
        result.validate()
        return result
    # trailing comment inside the class
'''

STRINGS = """TEMPLATE = '''
# a hash inside a string
def not_a_definition():
    pass
'''


def render(values, width):
    text = TEMPLATE.format(**values)  # format it
    lines = [line.ljust(width) for line in text.splitlines()]
    joined = "\\n".join(lines) + \\
        "end"
    return ("prefix " "suffix", joined, b"bytes # too");


def unicode_name(café, naïve):
    \"\"\"Unicode docstring é.\"\"\"
    total = café + naïve  # é comment
    extra = "ü" * 3
    if total:
        return total, extra
    return None, extra


if True:
    def guarded(first, second):
        third = first + second
        fourth = third * 2
        return fourth
"""

SOURCES = {"decorated": DECORATED, "classes": CLASSES, "strings": STRINGS}
TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\S")
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
AstSpan = tuple[tuple[int, int], tuple[int, int]]


def docstring_spans(tree: ast.Module) -> list[AstSpan]:
    spans: list[AstSpan] = []
    for node in ast.walk(tree):
        if not isinstance(node, DOCUMENTED) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.end_lineno is not None
            and first.end_col_offset is not None
        ):
            spans.append((
                (first.lineno, first.col_offset),
                (first.end_lineno, first.end_col_offset),
            ))
    return spans


def comment_spans(source: str, lines: list[str]) -> list[AstSpan]:
    spans: list[AstSpan] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            (row, start), (_, end) = token.start, token.end
            text = lines[row - 1]
            spans.append((
                (row, len(text[:start].encode())),
                (row, len(text[:end].encode())),
            ))
    return spans


def cleaned_lines(source: str, tree: ast.Module) -> list[bytearray]:
    lines = source.splitlines(keepends=True)
    encoded = [bytearray(line.encode()) for line in lines]
    for (first_row, first_col), (last_row, last_col) in [
        *docstring_spans(tree),
        *comment_spans(source, lines),
    ]:
        for row in range(first_row, last_row + 1):
            line = encoded[row - 1]
            start = first_col if row == first_row else 0
            end = last_col if row == last_row else len(line.rstrip(b"\r\n"))
            line[start:end] = b" " * (end - start)
    return encoded


def segment(lines: list[bytearray], node: ast.stmt) -> str:
    first, last = node.lineno, node.end_lineno or node.lineno
    rows = [bytes(line) for line in lines[first - 1 : last]]
    rows[-1] = rows[-1][: node.end_col_offset]
    rows[0] = rows[0][node.col_offset :]
    return b"".join(rows).decode(errors="replace")


def named_nodes(tree: ast.Module) -> list[tuple[str, ast.stmt]]:
    nodes: list[tuple[str, ast.stmt]] = []
    for node in tree.body:
        if isinstance(node, FUNCTIONS):
            nodes.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            nodes.append((node.name, node))
            nodes.extend(
                (f"{node.name}.{method.name}", method)
                for method in node.body
                if isinstance(method, FUNCTIONS)
            )
    return nodes


def ast_definitions(source: str) -> list[Definition]:
    starts = [0]
    for line in source.splitlines(keepends=True):
        starts.append(starts[-1] + len(line.encode()))
    found = []
    for name, node in named_nodes(ast.parse(source)):
        end_line = node.end_lineno or node.lineno
        end_column = node.end_col_offset or 0
        span = (
            starts[node.lineno - 1] + node.col_offset,
            starts[end_line - 1] + end_column,
        )
        found.append(Definition(name, node.lineno, end_line, span))
    return found


def kept(source: str) -> list[tuple[str, frozenset[tokens.Gram]]]:
    return [
        (item.name, grams)
        for item, grams in tokens.definition_grams("m.py", source)
        if item.end_line - item.line + 1 >= overlap.MIN_LINES
        and len(grams) >= overlap.MIN_GRAMS
    ]


def current(source: str) -> list[tuple[str, frozenset[tokens.Gram]]]:
    tree = ast.parse(source)
    lines = cleaned_lines(source, tree)
    found = []
    for name, node in named_nodes(tree):
        if (node.end_lineno or node.lineno) - node.lineno + 1 < overlap.MIN_LINES:
            continue
        found_tokens = TOKEN.findall(segment(lines, node))
        grams = frozenset(
            zip(found_tokens, found_tokens[1:], found_tokens[2:], strict=False)
        )
        if len(grams) >= overlap.MIN_GRAMS:
            found.append((name, grams))
    return found


class EquivalenceTests(unittest.TestCase):
    def assert_equivalent(self, source: str) -> None:
        root = parse("m.py", source)
        assert python.definitions(root, source) == ast_definitions(source)
        assert tokens.cleaned(
            source, tokens.removed_spans("python", root, source)
        ) == b"".join(cleaned_lines(source, ast.parse(source)))
        assert kept(source) == current(source)
        assert [
            (item.name, item.grams) for item in overlap.definitions("u", "m.py", source)
        ] == current(source)

    def test_representative_sources_match_the_ast_reader(self) -> None:
        for label, source in SOURCES.items():
            with self.subTest(source=label):
                self.assert_equivalent(source)
                assert current(source)

    def test_the_representative_sources_cover_each_shape(self) -> None:
        names = [
            item.name
            for source in SOURCES.values()
            for item in python.definitions(parse("m.py", source), source)
        ]
        assert names == [
            "summarise_orders",
            "fetch_rows",
            "Ledger",
            "Ledger.__init__",
            "Ledger.balance",
            "Ledger.load",
            "render",
            "unicode_name",
        ]

    def test_engine_sources_match_the_ast_reader(self) -> None:
        paths = sorted((ENGINE / "guardrails").rglob("*.py"))
        assert len(paths) > 100
        for path in paths:
            with self.subTest(path=path.relative_to(ENGINE).as_posix()):
                self.assert_equivalent(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
