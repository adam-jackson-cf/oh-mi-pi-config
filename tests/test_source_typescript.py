from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.structure_naming import typescript
from guardrails.commands.structure_naming.typescript import ScriptImport
from guardrails.foundation.source.tree import parse

if TYPE_CHECKING:
    from ast_grep_py import SgNode

pytestmark = pytest.mark.fast


def texts(source: str, spans: list[tuple[int, int]]) -> list[str]:
    data = source.encode()
    return [data[start:end].decode() for start, end in spans]


IMPORTS = """import type {A} from "./a";
import b, {type C, d as e} from '../b.js';
import * as ns from "@x/y";
import "./side";
import fs = require("fs");
export * from "./all";
export {x} from "./x";
export type {T} from "./t";
export const local = 1;
const m = await import("./dyn");
const r = require("./req");
const name = "./computed";
const n = import(name);
const o = require(`./template`);
other("./not-an-import");
"""


class ImportTests(unittest.TestCase):
    def test_static_dynamic_and_require_imports_with_string_literals(self) -> None:
        root: SgNode = parse("m.ts", IMPORTS)
        assert typescript.imports(root) == [
            ScriptImport("./a", 1, True),
            ScriptImport("../b.js", 2, False),
            ScriptImport("@x/y", 3, False),
            ScriptImport("./side", 4, False),
            ScriptImport("fs", 5, False),
            ScriptImport("./all", 6, False),
            ScriptImport("./x", 7, False),
            ScriptImport("./t", 8, True),
            ScriptImport("./dyn", 10, False),
            ScriptImport("./req", 11, False),
        ]

    def test_javascript_and_tsx_imports(self) -> None:
        source = "import React from 'react'\nconst x = require('./x')\n"
        for path in ("m.js", "m.cjs", "m.jsx", "m.tsx"):
            with self.subTest(path=path):
                assert [
                    item.specifier for item in typescript.imports(parse(path, source))
                ] == ["react", "./x"]


DEFINITIONS = """@sealed
export class Store extends Base {
  @logged() save(): void {}
  get size() { return 1 }
  #secret() {}
  static make = () => new Store();
  constructor() { super() }
}
export abstract class Shape { abstract area(): number; describe() { return 1 } }
export default function main() {}
function* generate() {}
async function load() {}
export const add = (a: number) => a, sub = function (a: number) { return a };
let later = async () => {};
var old = () => 1;
const value = 1;
const {destructured} = () => 1;
declare function ambient(): void;
export default class {}
if (ready) { function guarded() {} }
"""


class DefinitionTests(unittest.TestCase):
    def test_top_level_functions_classes_methods_and_function_values(self) -> None:
        found = typescript.definitions(parse("m.ts", DEFINITIONS), DEFINITIONS)
        assert [(item.name, item.line, item.end_line) for item in found] == [
            ("Store", 2, 8),
            ("Store.save", 3, 3),
            ("Store.size", 4, 4),
            ("Store.#secret", 5, 5),
            ("Store.constructor", 7, 7),
            ("Shape", 9, 9),
            ("Shape.describe", 9, 9),
            ("main", 10, 10),
            ("generate", 11, 11),
            ("load", 12, 12),
            ("add", 13, 13),
            ("sub", 13, 13),
            ("later", 14, 14),
        ]

    def test_spans_cover_the_declaration_without_decorators(self) -> None:
        found = typescript.definitions(parse("m.ts", DEFINITIONS), DEFINITIONS)
        spans = dict(
            zip(
                (item.name for item in found),
                texts(DEFINITIONS, [item.span for item in found]),
                strict=True,
            )
        )
        assert spans["Store"].startswith("class Store extends Base {")
        assert spans["Store"].endswith("}")
        assert spans["Store.save"] == "save(): void {}"
        assert spans["add"] == "add = (a: number) => a"
        assert spans["main"] == "function main() {}"

    def test_undecorated_and_unexported_classes_in_tsx(self) -> None:
        source = "@dec\nclass View { render() { return <div>é</div> } }\n"
        found = typescript.definitions(parse("v.tsx", source), source)
        assert [item.name for item in found] == ["View", "View.render"]
        assert texts(source, [found[0].span]) == [
            "class View { render() { return <div>é</div> } }"
        ]


class CommentAndIdentifierTests(unittest.TestCase):
    def test_line_and_block_comments(self) -> None:
        source = "/* block\n é */ const a = '// not' // line\n"
        spans = typescript.comment_spans(parse("m.ts", source), source)
        assert texts(source, spans) == ["/* block\n é */", "// line"]

    def test_identifier_kinds(self) -> None:
        source = (
            "class Box { #inner = 1 }\n"
            "type Label = string\n"
            "const {width} = shape\n"
            "const item = {height, size: shape.depth}\n"
        )
        assert typescript.identifiers(parse("m.ts", source), "typescript") == [
            "Box",
            "#inner",
            "Label",
            "width",
            "shape",
            "item",
            "height",
            "size",
            "depth",
        ]


if __name__ == "__main__":
    unittest.main()
