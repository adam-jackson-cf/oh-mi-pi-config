from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.foundation.source import python
from guardrails.foundation.source.python import PythonImport
from guardrails.foundation.source.tree import parse

if TYPE_CHECKING:
    from ast_grep_py import SgNode

pytestmark = pytest.mark.fast


def root_of(source: str) -> SgNode:
    return parse("m.py", source)


def texts(source: str, spans: list[tuple[int, int]]) -> list[str]:
    data = source.encode()
    return [data[start:end].decode() for start, end in spans]


IMPORTS = """from __future__ import annotations
import a.b as c, d
from . import (x, y as z)
from ..m.n import *
from .k import j
from p.q import r


def f():
    import inner
"""


class ImportTests(unittest.TestCase):
    def test_every_import_form_keeps_module_names_level_and_line(self) -> None:
        assert python.imports(root_of(IMPORTS)) == [
            PythonImport("from", "__future__", ("annotations",), 0, 1),
            PythonImport("import", "a.b", (), 0, 2),
            PythonImport("import", "d", (), 0, 2),
            PythonImport("from", "", ("x", "y"), 1, 3),
            PythonImport("from", "m.n", ("*",), 2, 4),
            PythonImport("from", "k", ("j",), 1, 5),
            PythonImport("from", "p.q", ("r",), 0, 6),
            PythonImport("import", "inner", (), 0, 10),
        ]


DEFINITIONS = """import functools


@functools.cache
async def load(name):
    return name


class Store:
    kind = 1

    @property
    def size(self):
        def inner():
            return 1
        return inner()

    class Nested:
        def hidden(self):
            pass

    async def fetch(self):
        pass
    # trailing comment


if True:
    def guarded():
        pass

value = lambda: 1
"""


class DefinitionTests(unittest.TestCase):
    def test_top_level_functions_classes_and_their_methods(self) -> None:
        found = python.definitions(root_of(DEFINITIONS), DEFINITIONS)
        assert [(item.name, item.line, item.end_line) for item in found] == [
            ("load", 5, 6),
            ("Store", 9, 23),
            ("Store.size", 13, 16),
            ("Store.fetch", 22, 23),
        ]

    def test_spans_start_at_the_keyword_and_end_at_the_last_statement(self) -> None:
        found = python.definitions(root_of(DEFINITIONS), DEFINITIONS)
        spans = texts(DEFINITIONS, [item.span for item in found])
        assert spans[0] == "async def load(name):\n    return name"
        assert spans[1].startswith("class Store:")
        assert spans[1].endswith("pass")
        assert spans[2].startswith("def size(self):")
        assert spans[3] == "async def fetch(self):\n        pass"

    def test_a_span_keeps_a_final_semicolon_but_not_a_final_comment(self) -> None:
        source = (
            "def f():\n    x = 1;\n\n\ndef g():\n    return (1 +\n            2)  # c\n"
        )
        found = python.definitions(root_of(source), source)
        assert texts(source, [item.span for item in found]) == [
            "def f():\n    x = 1;",
            "def g():\n    return (1 +\n            2)",
        ]

    def test_spans_are_utf8_byte_offsets(self) -> None:
        source = "x = 'é'\n\n\ndef f():\n    return 'ü'\n"
        [item] = python.definitions(root_of(source), source)
        start, end = item.span
        assert source.encode()[start:end] == "def f():\n    return 'ü'".encode()


DOCSTRINGS = '''"""Module."""
import x


class A:
    # comment first
    """Class."""

    def m(self):
        ("""Paren.""")

    def n(self):
        "a" "b"

    def o(self):
        f"{x}"

    def p(self):
        b"bytes"

    def q(self):
        x = 1
        """Not first."""


def outer():
    def inner():
        r"""Raw."""
'''


class DocstringTests(unittest.TestCase):
    def test_first_text_statement_of_module_class_and_function_bodies(self) -> None:
        spans = python.docstring_spans(root_of(DOCSTRINGS), DOCSTRINGS)
        assert sorted(texts(DOCSTRINGS, spans)) == sorted([
            '"""Module."""',
            '"""Class."""',
            '("""Paren.""")',
            '"a" "b"',
            'r"""Raw."""',
        ])

    def test_a_module_without_statements_has_no_docstring(self) -> None:
        assert python.docstring_spans(root_of("# only\n"), "# only\n") == []


class CommentAndIdentifierTests(unittest.TestCase):
    def test_comment_spans_cover_each_comment(self) -> None:
        source = "#!/usr/bin/env python\nx = '#no'  # yes é\n"
        spans = python.comment_spans(root_of(source), source)
        assert texts(source, spans) == ["#!/usr/bin/env python", "# yes é"]

    def test_identifiers_are_unique_in_source_order(self) -> None:
        source = "import os.path as p\ndef run(arg):\n    total = arg.size + arg\n"
        assert python.identifiers(root_of(source)) == [
            "os",
            "path",
            "p",
            "run",
            "arg",
            "total",
            "size",
        ]


if __name__ == "__main__":
    unittest.main()
