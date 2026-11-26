"""Definitions and alias bindings the structure-naming name extractor reads from each language."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.structure_naming.name_pair import extract
from guardrails.context.config.suite_files import is_test_source


def in_test_source(path: str) -> bool:
    return is_test_source(path, {})


GO = """// Package store keeps users.
package store

import "fmt"

// Store holds users.
type Store struct {
\tname string
}

type ID = string
type Handle = *Store
type Pair[T any] struct{ a T }

// Get returns a user.
func (s *Store) Get(id ID) string {
\treturn fmt.Sprint("{", id, "}")
}

func Lookup[T any](id T) T { return id }

const Limit = 10
const Label = "users"
var _ = Lookup[int]
var Fetch = Lookup
var Default = true

const (
\tFirst = iota
\tSecond = Fetch
)
var (
\tCached = "x"
)
"""

RUST = """//! crate root
use crate::store::Store as Repo;
use self::inner::Thing as Thing;
use crate::other::Item as _;
use std::fmt as f;

/// A user store.
#[derive(Debug)]
pub struct Users {
    items: Vec<String>,
}

pub(crate) enum Mode { A, B }

pub type Alias = crate::store::Store;
type Strange<T> = Vec<T>;

pub const NAME: &str = "users";
static COUNT: usize = 3;

impl Users {
    /// Make one.
    pub fn new() -> Self {
        Users { items: vec![] }
    }
        fn deep() {}
}

impl<T> Display for Mode {
    fn fmt(&self) {}
}

pub async fn load() {}
fn helper() {}
"""

SCRIPT = """/**
 * The api module.
 */
import { fetchUser as getUser, other } from "./store";
import { thing as alias2 } from "lodash";
import type { A as B } from "./types";
export { load as loadAll, default as main, same as same } from "./load";
export async function fetchAll() {}
export default class Client extends base.Service {
}
abstract class Plain {}
export interface Shape {}
declare enum Colour {}
export type Id = string;
export type Ref = Shape;
export type Box<T> = { v: T };
export const handler = async (req) => req;
const arrow = (x: number): number => x;
const named = function () {};
export const getAll = fetchAll;
const flag = true;
export const LIMIT = "ten";
let lower = 3;
const same = same;
"""

PYTHON = '''"""Store module."""
from __future__ import annotations
import os.path as osp
import shop.util as helpers
import shop.util as util
import shop.util
from shop.util import (
    load as fetch,
    save,
    keep as keep,
)
from . import rows as records
from .rows import *
from os import path as p


def fetch_user(
    uid,
):
    """Return one user."""
    return uid


@cached
async def load_all():
    pass


class Store(Base[int], mixins.Mixin):
    """Keeps users."""

    lookup = fetch_user
    same = same
    __slots__ = ()
    a = b = fetch_user
    count = 3
    path = os.path

    def __init__(self):
        pass

    def get(self, uid):
        """One user."""
        return uid

    async def all(self):
        return []


MAX_USERS = 10
LABEL = "users"
__all__ = ["Store"]
get_user = fetch_user
fetch_user2: Callable = fetch_user
UserMap = dict[str, int]
Either = int | str
x, y = 1, 2
loader: Callable
lower = 3
'''


def extracted(
    tree: dict[str, str],
) -> tuple[set[tuple[Any, ...]], set[tuple[Any, ...]]]:
    found = extract.extract_tree(tree, in_test_source)
    definitions = {
        (item["path"], item["name"], item["kind"], item["line"], item["end"])
        for item in found.definitions
    }
    aliases = {
        (item["path"], item["alias"], item["target"], item["how"], item["internal"])
        for item in found.aliases
    }
    return definitions, aliases


@pytest.mark.fast
class WordTests(unittest.TestCase):
    def test_words_split_case_and_separators_and_stem_plurals(self) -> None:
        assert [
            extract.stem(word) for word in ("policies", "class", "users", "ies")
        ] == [
            "policy",
            "class",
            "user",
            "ies",
        ]
        assert extract.words("shop.store.fetchUser") == ["fetch", "user"]
        assert extract.words("shop.store.fetchUser", keep_dots=True) == [
            "shop",
            "store",
            "fetch",
            "user",
        ]
        assert extract.words('"a.b"') == ["a", "b"]

    def test_module_roots_are_python_folders_and_src_layout_packages(self) -> None:
        assert extract.module_roots([
            "a.py",
            "src/pkg/m.py",
            "lib/x.py",
            "web/a.ts",
        ]) == {"a", "src", "pkg", "lib", "x"}
        assert (
            extract.language_of("a.go"),
            extract.language_of("a.mjs"),
        ) == (
            "go",
            "ts",
        )


@pytest.mark.fast
class GoExtractionTests(unittest.TestCase):
    def test_functions_methods_types_values_and_aliases(self) -> None:
        definitions, aliases = extracted({"svc/store.go": GO})
        assert {item[1:4] for item in definitions} == {
            ("store", "module", 1),
            ("Store", "class", 7),
            ("Pair", "class", 13),
            ("Store.Get", "method", 16),
            ("Lookup", "function", 20),
            ("Limit", "constant", 22),
            ("Label", "constant", 23),
            ("Default", "constant", 26),
            ("First", "constant", 29),
            ("Cached", "constant", 33),
        }
        assert {item[1:4] for item in aliases} == {
            ("ID", "string", "type_alias"),
            ("Handle", "Store", "type_alias"),
            ("Fetch", "Lookup", "assign"),
            ("Second", "Fetch", "assign"),
        }

    def test_a_definition_carries_its_comment_value_and_block_end(self) -> None:
        found = extract.extract_tree({"svc/store.go": GO}, in_test_source).definitions
        by_name = {item["name"]: item for item in found}
        assert (by_name["Store"]["doc"], by_name["Store"]["end"]) == (
            "Store holds users.",
            9,
        )
        assert by_name["Store.Get"]["owner"] == "Store"
        assert not by_name["Store.Get"]["top"]
        assert by_name["Label"]["value"] == "users"
        assert by_name["Store.Get"]["end"] == 18


@pytest.mark.fast
class RustExtractionTests(unittest.TestCase):
    def test_items_impl_methods_constants_and_internal_use_aliases(self) -> None:
        definitions, aliases = extracted({"svc/lib.rs": RUST})
        assert {item[1:4] for item in definitions} == {
            ("Users", "class", 9),
            ("Mode", "class", 13),
            ("NAME", "constant", 18),
            ("COUNT", "constant", 19),
            ("Users.new", "method", 23),
            ("Mode.fmt", "method", 30),
            ("load", "function", 33),
            ("helper", "function", 34),
        }
        assert {item[1:] for item in aliases} == {
            ("Repo", "Store", "use_as", True),
            ("Alias", "Store", "type_alias", None),
        }

    def test_doc_comments_skip_attributes(self) -> None:
        found = extract.extract_tree({"svc/lib.rs": RUST}, in_test_source).definitions
        by_name = {item["name"]: item for item in found}
        assert by_name["Users"]["doc"] == "A user store."
        assert by_name["Users.new"]["doc"] == "Make one."
        assert by_name["NAME"]["value"] == "users"


@pytest.mark.fast
class ScriptExtractionTests(unittest.TestCase):
    def test_functions_classes_types_bindings_and_relative_renames(self) -> None:
        tree = {
            "web/api.ts": SCRIPT,
            "web/index.ts": "export const x = 1;\n",
            "web/widget.spec.ts": "export const Y = 1;\n",
        }
        definitions, aliases = extracted(tree)
        assert {item[1:3] for item in definitions} == {
            ("api", "module"),
            ("fetchAll", "function"),
            ("Client", "class"),
            ("Plain", "class"),
            ("Shape", "type"),
            ("Colour", "type"),
            ("Box", "type"),
            ("handler", "function"),
            ("arrow", "function"),
            ("named", "function"),
            ("LIMIT", "constant"),
        }
        assert {item[1:] for item in aliases} == {
            ("getUser", "fetchUser", "import_as", True),
            ("B", "A", "import_as", True),
            ("loadAll", "load", "reexport_as", True),
            ("Id", "string", "type_alias", None),
            ("Ref", "Shape", "type_alias", None),
            ("getAll", "fetchAll", "assign", None),
        }

    def test_a_class_records_its_base_and_a_constant_its_string(self) -> None:
        found = extract.extract_tree({"web/api.ts": SCRIPT}, in_test_source).definitions
        by_name = {item["name"]: item for item in found}
        assert by_name["Client"]["bases"] == ["Service"]
        assert by_name["LIMIT"]["value"] == "ten"


@pytest.mark.fast
class PythonExtractionTests(unittest.TestCase):
    def test_definitions_members_constants_and_internal_aliases(self) -> None:
        tree = {
            "src/shop/__init__.py": "",
            "src/shop/store.py": PYTHON,
            "src/shop/broken.py": "def broken(:\n",
            "tools/run.sh": "echo hi\n",
        }
        definitions, aliases = extracted(tree)
        assert {item[1:5] for item in definitions} == {
            ("store", "module", 1, 60),
            ("fetch_user", "function", 17, 21),
            ("load_all", "function", 24, 26),
            ("Store", "class", 29, 47),
            ("Store.get", "method", 42, 44),
            ("Store.all", "method", 46, 47),
            ("MAX_USERS", "constant", 50, 50),
            ("LABEL", "constant", 51, 51),
            ("UserMap", "type", 55, 55),
            ("Either", "type", 56, 56),
        }
        assert {item[1:] for item in aliases} == {
            ("helpers", "util", "import_module_as", True),
            ("fetch", "load", "import_as", True),
            ("records", "rows", "import_as", True),
            ("Store.lookup", "fetch_user", "class_assign", True),
            ("get_user", "fetch_user", "assign", None),
            ("fetch_user2", "fetch_user", "assign", None),
        }

    def test_signatures_span_their_lines_and_classes_keep_their_bases(self) -> None:
        found = extract.extract_tree(
            {"src/shop/store.py": PYTHON}, in_test_source
        ).definitions
        by_name = {item["name"]: item for item in found}
        assert by_name["fetch_user"]["sig"] == "def fetch_user(\n    uid,\n):"
        assert by_name["load_all"]["line"] == 24
        assert by_name["Store"]["bases"] == ["Base", "Mixin"]
        assert by_name["LABEL"]["value"] == "users"
        assert by_name["MAX_USERS"]["value"] is None


if __name__ == "__main__":
    unittest.main()
