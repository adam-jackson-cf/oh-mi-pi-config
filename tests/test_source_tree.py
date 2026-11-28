from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.structure_naming import tokens
from guardrails.foundation.source.tree import (
    Definition,
    Offsets,
    SourceError,
    grammar,
    language_of,
    named,
    parse,
    source_language,
)

pytestmark = pytest.mark.fast


class LanguageTests(unittest.TestCase):
    def test_extensions_map_to_their_language_family(self) -> None:
        expected = {
            "a/b.py": "python",
            "b.pyi": "python",
            "c.ts": "typescript",
            "c.mts": "typescript",
            "c.cts": "typescript",
            "c.tsx": "typescript",
            "c.js": "typescript",
            "c.jsx": "typescript",
            "c.mjs": "typescript",
            "c.cjs": "typescript",
            "pkg/main.go": "go",
        }
        for path, language in expected.items():
            with self.subTest(path=path):
                assert language_of(path) == language

    def test_other_files_have_no_language(self) -> None:
        for path in ("run.sh", "data.json", "README.md", "Makefile"):
            with self.subTest(path=path):
                assert language_of(path) is None
        with pytest.raises(SourceError, match="run.sh"):
            source_language("run.sh")

    def test_each_extension_selects_its_grammar(self) -> None:
        expected = {
            "a.py": "python",
            "a.pyi": "python",
            "a.ts": "typescript",
            "a.mts": "typescript",
            "a.cts": "typescript",
            "a.tsx": "tsx",
            "a.jsx": "tsx",
            "a.js": "javascript",
            "a.mjs": "javascript",
            "a.cjs": "javascript",
            "a.go": "go",
        }
        for path, language in expected.items():
            with self.subTest(path=path):
                assert grammar(path) == language


class ParseTests(unittest.TestCase):
    def test_valid_sources_parse_to_their_root(self) -> None:
        sources = {
            "a.py": "x = 1\n",
            "a.ts": "const a: number = 1\n",
            "a.tsx": "const a = <div>{x}</div>\n",
            "a.jsx": "const a = <A b='c' />\n",
            "a.js": "const a = require('x')\n",
            "a.go": "package a\n\nvar r = ``\n",
        }
        for path, source in sources.items():
            with self.subTest(path=path):
                assert parse(path, source).text() == source

    def test_empty_files_parse(self) -> None:
        for path in ("a.py", "a.ts", "a.js"):
            with self.subTest(path=path):
                assert named(parse(path, "")) == []

    def test_error_nodes_are_parse_errors_with_their_line(self) -> None:
        with pytest.raises(SourceError, match=r"cannot parse a\.py as python: line 2"):
            parse("a.py", "x = 1\ny = (1,\n")
        with pytest.raises(SourceError, match="line 1"):
            parse("a.ts", "foo(a b)\n")
        with pytest.raises(SourceError, match="as go"):
            parse("a.go", "package a\nfunc {\n")

    def test_missing_nodes_are_parse_errors(self) -> None:
        with pytest.raises(SourceError, match="line 1"):
            parse("a.py", "def f(:\n")

    def test_typescript_syntax_is_not_valid_javascript(self) -> None:
        with pytest.raises(SourceError):
            parse("a.js", "const a: number = 1\n")
        assert parse("a.ts", "const a: number = 1\n").text()

    def test_unknown_languages_are_not_parsed(self) -> None:
        with pytest.raises(SourceError, match="not Python, TypeScript or Go"):
            parse("a.sh", "echo hi\n")


class OffsetTests(unittest.TestCase):
    def test_spans_count_utf8_bytes(self) -> None:
        source = "x = 'é'\ny = 1\n"
        root = parse("a.py", source)
        second = named(root)[1]
        start, end = Offsets(source).span(second)
        assert source.encode()[start:end] == b"y = 1"

    def test_ascii_spans_are_character_indexes(self) -> None:
        source = "x = 1\ny = 2\n"
        second = named(parse("a.py", source))[1]
        assert Offsets(source).span(second) == (6, 11)


class TokenTests(unittest.TestCase):
    def test_cleaning_blanks_spans_but_keeps_line_endings(self) -> None:
        assert tokens.cleaned("ab # cé\r\nd\n", [(3, 8)]) == b"ab      \r\nd\n"
        assert tokens.cleaned("a/* x\n y */b", [(1, 11)]) == b"a    \n     b"

    def test_grams_are_token_triples(self) -> None:
        assert tokens.grams("total = a+b") == frozenset({
            ("total", "=", "a"),
            ("=", "a", "+"),
            ("a", "+", "b"),
        })
        assert tokens.grams("a b") == frozenset()

    def test_segment_is_the_definition_span_of_the_cleaned_text(self) -> None:
        item = Definition("f", 1, 1, (2, 5))
        assert tokens.segment(b"x def y", item) == "def"

    def test_python_definitions_drop_comments_and_docstrings(self) -> None:
        source = 'def total(a, b):\n    """Sum two."""\n    # add\n    return a + b  # done\n'
        [(item, grams)] = tokens.definition_grams("m.py", source)
        assert item.name == "total"
        words = {token for gram in grams for token in gram}
        assert {"Sum", "two", "add", "done", "#"}.isdisjoint(words)
        assert ("return", "a", "+") in grams

    def test_typescript_definitions_drop_comments(self) -> None:
        source = "/** doc */\nexport function total(a: number) {\n  // add\n  return a + 1 /* one */\n}\n"
        [(item, grams)] = tokens.definition_grams("m.ts", source)
        assert item.name == "total"
        words = {token for gram in grams for token in gram}
        assert {"doc", "add", "one"}.isdisjoint(words)
        assert ("return", "a", "+") in grams

    def test_go_definitions_drop_comments(self) -> None:
        source = "package a\n\n// Total adds.\nfunc Total(a int) int {\n\treturn a + 1 // one\n}\n"
        [(item, grams)] = tokens.definition_grams("a.go", source)
        assert item.name == "Total"
        words = {token for gram in grams for token in gram}
        assert {"adds", "one"}.isdisjoint(words)
        assert ("return", "a", "+") in grams

    def test_go_strings_are_not_comments(self) -> None:
        source = 'package a\n\nfunc F() string {\n\treturn "// kept"\n}\n'
        [(_, grams)] = tokens.definition_grams("a.go", source)
        assert ('"', "/", "/") in grams


class ArgumentOrderTests(unittest.TestCase):
    def test_python_argument_order_matches_cpython(self) -> None:
        for source in ("f(a=1, b)\n", "f(**k, *a)\n", "f(**k, b)\n"):
            with self.subTest(source=source), pytest.raises(SourceError):
                parse("m.py", source)
        for source in ("f(*a, b=1, **k)\n", "f(a, *b, c=1)\n", "f(a=1, *b)\n"):
            with self.subTest(source=source):
                parse("m.py", source)


if __name__ == "__main__":
    unittest.main()
