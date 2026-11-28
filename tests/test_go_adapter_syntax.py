from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.adapters.go.cases import CollectionError, collect
from guardrails.commands.adapters.go.syntax import defined_tests
from guardrails.foundation.source.tree import SourceError

pytestmark = pytest.mark.fast

SOURCE = """package shop

import "testing"

const listing = `
func TestInsideRawString(t *testing.T) {
`

// func TestInsideComment(t *testing.T) {

func TestTotal(t *testing.T) {}

func FuzzParse(f *testing.F) {}

func ExampleTotal() {}

func Testing() {}

func (s suite) TestMethod(t *testing.T) {}

func helper() {}
"""


class DefinedTestTests(unittest.TestCase):
    def test_only_top_level_test_functions_are_definitions(self) -> None:
        assert defined_tests("shop/total_test.go", SOURCE) == [
            "TestTotal",
            "FuzzParse",
            "ExampleTotal",
        ]

    def test_a_file_that_does_not_parse_names_its_path(self) -> None:
        with pytest.raises(SourceError, match="shop/total_test.go as go: line 3"):
            defined_tests("shop/total_test.go", "package shop\n\nfunc (\n")


class CollectionTests(unittest.TestCase):
    def test_a_missing_token_is_a_parse_error(self) -> None:
        with pytest.raises(CollectionError, match="does not parse as Go"):
            collect(
                "shop/total_test.go", "package shop\n\nfunc TestTotal(t *testing.T) {\n"
            )


if __name__ == "__main__":
    unittest.main()
