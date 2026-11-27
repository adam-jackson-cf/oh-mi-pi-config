"""Comments of every file type: the tree's comment nodes for the structure languages and
the convention packs' comment syntax for the rest."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.foundation import conventions
from guardrails.foundation.source import comments
from guardrails.foundation.source.tree import parse

pytestmark = pytest.mark.fast


def lexed(path: str, text: str) -> list[tuple[int, int, str]]:
    found = comments.lexed(text, conventions.comment_syntax(path))
    return [(item.line, item.end_line, item.text) for item in found]


def tree_comments(path: str, text: str) -> list[tuple[int, int, str]]:
    found = comments.tree_comments(parse(path, text), conventions.comment_syntax(path))
    return [(item.line, item.end_line, item.text) for item in found]


class TreeCommentTests(unittest.TestCase):
    def test_a_go_raw_string_ending_in_a_backslash_does_not_hide_a_comment(
        self,
    ) -> None:
        source = "package a\n\nvar root = `C:\\` // drive\n"
        assert tree_comments("a.go", source) == [(3, 3, " drive")]

    def test_script_markers_are_stripped_and_block_lines_kept(self) -> None:
        source = "/** one\n * two */\nconst re = /\\/\\//; // tail\n"
        assert tree_comments("a.ts", source) == [
            (1, 2, "* one\n * two "),
            (3, 3, " tail"),
        ]

    def test_python_comments_lose_their_hash(self) -> None:
        assert tree_comments("a.py", "x = '#' # note\n") == [(1, 1, " note")]


class LexedCommentTests(unittest.TestCase):
    def test_quoted_markers_are_no_comments(self) -> None:
        assert lexed("a.yaml", 'key: "# no" # yes\n') == [(1, 1, " yes")]
        assert lexed("a.sql", "SELECT 'it''s -- no'; -- yes\n") == [(1, 1, " yes")]

    def test_the_longer_marker_opens_first(self) -> None:
        assert lexed("a.lua", "--[[ block\n]] x = 1 -- line\n") == [
            (1, 2, " block\n"),
            (2, 2, " line"),
        ]

    def test_shell_comments_start_a_word(self) -> None:
        assert lexed("run.sh", "echo $# ${#a} x#y # note\n") == [(1, 1, " note")]

    def test_markup_has_no_string_literals(self) -> None:
        assert lexed("a.md", "it's <!-- note -->\n") == [(1, 1, " note ")]

    def test_tool_file_names_match_case_insensitively(self) -> None:
        assert lexed("build/dockerfile", "# note\nFROM scratch\n") == [(1, 1, " note")]
        assert conventions.parser_directive_file("ci/app.Dockerfile")

    def test_a_file_type_without_comment_syntax_has_no_comments(self) -> None:
        assert lexed("data.json", '{"a": "# no"}\n') == []


if __name__ == "__main__":
    unittest.main()
