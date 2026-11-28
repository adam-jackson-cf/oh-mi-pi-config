from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.foundation.source import go
from guardrails.foundation.source.go import GoImport
from guardrails.foundation.source.tree import SourceError, parse

pytestmark = pytest.mark.fast

SOURCE = """//go:build ignore

// Package server runs things.
package server

import (
\t"fmt"
\tstr "strings"
\t_ "embed"
)
import "example.com/app/internal/store"

//go:embed static/*.html "with space.txt"
//go:embed `raw.txt` templates
var files string

func (s *Server[T]) Run(x int) error { return nil }

func (Plain) Stop() {}

func (p *(Paren)) Wrapped() {}

func Top() { /* inner é */ }

type Server[T any] struct{ field int }
"""


def texts(source: str, spans: list[tuple[int, int]]) -> list[str]:
    data = source.encode()
    return [data[start:end].decode() for start, end in spans]


class ImportTests(unittest.TestCase):
    def test_import_paths_with_lines_and_the_package_name(self) -> None:
        root = parse("server.go", SOURCE)
        assert go.imports(root) == [
            GoImport("fmt", 7),
            GoImport("strings", 8),
            GoImport("embed", 9),
            GoImport("example.com/app/internal/store", 11),
        ]
        assert go.package_name(root) == "server"

    def test_a_file_without_a_package_clause_is_an_error(self) -> None:
        with pytest.raises(SourceError, match="no package clause"):
            go.package_name(parse("a.go", "func F() {}\n"))


class DefinitionTests(unittest.TestCase):
    def test_functions_and_methods_named_by_their_receiver_type(self) -> None:
        found = go.definitions(parse("server.go", SOURCE), SOURCE)
        assert [(item.name, item.line, item.end_line) for item in found] == [
            ("Server.Run", 17, 17),
            ("Plain.Stop", 19, 19),
            ("Paren.Wrapped", 21, 21),
            ("Top", 23, 23),
        ]
        assert texts(SOURCE, [found[3].span]) == ["func Top() { /* inner é */ }"]


class CommentAndIdentifierTests(unittest.TestCase):
    def test_comment_spans(self) -> None:
        spans = go.comment_spans(parse("server.go", SOURCE), SOURCE)
        assert texts(SOURCE, spans) == [
            "//go:build ignore",
            "// Package server runs things.",
            '//go:embed static/*.html "with space.txt"',
            "//go:embed `raw.txt` templates",
            "/* inner é */",
        ]

    def test_identifier_kinds(self) -> None:
        source = 'package a\n\nimport f "fmt"\n\ntype T struct{ size int }\n\nfunc (t T) Show() { f.Println(t.size) }\n'
        assert go.identifiers(parse("a.go", source)) == [
            "a",
            "f",
            "T",
            "size",
            "int",
            "t",
            "Show",
            "Println",
        ]


class DirectiveTests(unittest.TestCase):
    def test_embed_patterns_from_every_directive(self) -> None:
        assert go.embed_patterns(parse("server.go", SOURCE)) == [
            "static/*.html",
            "with space.txt",
            "raw.txt",
            "templates",
        ]

    def test_quoted_embed_patterns_decode_go_escapes(self) -> None:
        source = 'package a\n\n//go:embed "esc\\x41.txt"\nvar f string\n'
        assert go.embed_patterns(parse("a.go", source)) == ["escA.txt"]

    def test_an_invalid_quoted_embed_pattern_is_an_error(self) -> None:
        source = 'package a\n\n//go:embed "bad\\q"\nvar f string\n'
        with pytest.raises(SourceError, match="invalid //go:embed pattern"):
            go.embed_patterns(parse("a.go", source))

    def test_build_ignore_before_the_package_clause(self) -> None:
        assert go.build_ignored(parse("server.go", SOURCE))
        cases = {
            "package a\n": False,
            "//go:build linux\n\npackage a\n": False,
            "//go:build ignore && linux\n\npackage a\n": False,
            "package a\n\n//go:build ignore\n": False,
            "// header\n\n//go:build  ignore \n\npackage a\n": True,
        }
        for source, ignored in cases.items():
            with self.subTest(source=source):
                assert go.build_ignored(parse("a.go", source)) is ignored

    def test_test_files_end_in_underscore_test(self) -> None:
        assert go.is_test_file("pkg/server_test.go")
        assert not go.is_test_file("pkg/server.go")
        assert not go.is_test_file("pkg/test.go")


if __name__ == "__main__":
    unittest.main()
