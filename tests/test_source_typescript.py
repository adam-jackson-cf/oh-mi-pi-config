from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import TYPE_CHECKING

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.foundation.source import typescript
from guardrails.foundation.source.tree import comment_spans, parse
from guardrails.foundation.source.typescript import Binding, ScriptImport

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


def bound(
    specifier: str, imported: str | None, local: str | None, form: typescript.Form
) -> Binding:
    return Binding(specifier, imported, local, form, False)


class ImportTests(unittest.TestCase):
    def test_static_dynamic_and_require_imports_with_string_literals(self) -> None:
        root: SgNode = parse("m.ts", IMPORTS)
        assert typescript.imports(root) == [
            ScriptImport("./a", 1, True, (Binding("./a", "A", "A", "named", True),)),
            ScriptImport(
                "../b.js",
                2,
                False,
                (
                    bound("../b.js", "default", "b", "default"),
                    Binding("../b.js", "C", "C", "named", True),
                    bound("../b.js", "d", "e", "named"),
                ),
            ),
            ScriptImport("@x/y", 3, False, (bound("@x/y", None, "ns", "namespace"),)),
            ScriptImport("./side", 4, False, ()),
            ScriptImport("fs", 5, False, (bound("fs", None, "fs", "import-equals"),)),
            ScriptImport(
                "./all", 6, False, (bound("./all", None, None, "export-from"),)
            ),
            ScriptImport("./x", 7, False, (bound("./x", "x", "x", "export-from"),)),
            ScriptImport(
                "./t", 8, True, (Binding("./t", "T", "T", "export-from", True),)
            ),
            ScriptImport("./dyn", 10, False, (bound("./dyn", None, "m", "dynamic"),)),
            ScriptImport("./req", 11, False, (bound("./req", None, "r", "require"),)),
            ScriptImport(
                "./template", 14, False, (bound("./template", None, "o", "require"),)
            ),
        ]

    def test_require_and_import_bindings_read_destructuring_and_members(self) -> None:
        source = (
            "const { a, b: c, d = 1, ...rest } = require('./m');\n"
            "const helper = require('./h').helper;\n"
            "const { k } = await import('./k');\n"
            "const p = (await import('./p')).value;\n"
            "import x = require('./x');\n"
            "export { y as z, default as w } from './y';\n"
            "export * as all from './all';\n"
            "require('./side');\n"
            "load(require('./argument'));\n"
        )
        found = {
            item.specifier: item.bindings
            for item in typescript.imports(parse("m.ts", source))
        }
        assert found == {
            "./m": (
                bound("./m", "a", "a", "require"),
                bound("./m", "b", "c", "require"),
                bound("./m", "d", "d", "require"),
            ),
            "./h": (bound("./h", "helper", "helper", "require"),),
            "./k": (bound("./k", "k", "k", "dynamic"),),
            "./p": (bound("./p", "value", "p", "dynamic"),),
            "./x": (bound("./x", None, "x", "import-equals"),),
            "./y": (
                bound("./y", "y", "z", "export-from"),
                bound("./y", "default", "w", "export-from"),
            ),
            "./all": (bound("./all", None, "all", "export-from"),),
            "./side": (),
            "./argument": (),
        }

    def test_javascript_and_tsx_imports(self) -> None:
        source = "import React from 'react'\nconst x = require('./x')\n"
        for path in ("m.js", "m.cjs", "m.jsx", "m.tsx"):
            with self.subTest(path=path):
                assert [
                    item.specifier for item in typescript.imports(parse(path, source))
                ] == ["react", "./x"]


def literal_value(text: str) -> str | None:
    root = parse("m.js", f"x = {text};\n")
    assignment = root.find(kind="assignment_expression")
    assert assignment is not None
    return typescript.literal(assignment.field("right"))


class LiteralTests(unittest.TestCase):
    def test_javascript_escapes(self) -> None:
        face = chr(0x1F600)
        cases = {
            r"'\u{1F600}'": face,
            r"'\uD83D\uDE00'": face,
            r"'\x41\u0042'": "AB",
            r"'a\d'": "ad",
            r"'\a\q\8'": "aq8",
            r"'\n\t\0'": "\n\t\0",
            r"'\101'": "A",
            r"""'it\'s \"quoted\"'""": 'it\'s "quoted"',
            "'line\\\ncontinued'": "linecontinued",
            r"`plain \`tick\` \u{41}`": "plain `tick` A",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                assert literal_value(text) == expected

    def test_dynamic_and_invalid_values_are_not_literals(self) -> None:
        for text in ("`a${b}`", "name", "1", r"'\u{110000}'"):
            with self.subTest(text=text):
                assert literal_value(text) is None


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


class CommentAndDeclarationTests(unittest.TestCase):
    def test_line_and_block_comments(self) -> None:
        source = "/* block\n é */ const a = '// not' // line\n"
        spans = comment_spans(parse("m.ts", source), source)
        assert texts(source, spans) == ["/* block\n é */", "// line"]

    def test_regex_and_template_text_is_no_comment(self) -> None:
        source = "const r = /\\/\\//;\nconst t = `// ${a /* x */}`;\nconst w = 'C:\\\\' // real\n"
        spans = comment_spans(parse("m.ts", source), source)
        assert texts(source, spans) == ["/* x */", "// real"]

    def test_bound_names_are_top_level_declarations_and_their_members(self) -> None:
        source = (
            'import { oracle } from "./oracle";\n'
            "export class Box extends base.Shape { #inner = 1; size = 2; grow(): void {} }\n"
            "interface Shape { area: number; scale(): void }\n"
            "type Label = { text: string };\n"
            "enum Mode { Fast, Slow = 2 }\n"
            "declare function measure(): number;\n"
            "export const { width, height: tall, ...rest } = shape;\n"
            "const [first, second = 1] = pair;\n"
            "namespace Tools { export const inner = 1 }\n"
            "function run() { const local = 1; return local }\n"
            "oracle.call();\n"
        )
        assert typescript.bound_names(parse("m.ts", source)) == [
            "Box",
            "#inner",
            "size",
            "grow",
            "Shape",
            "area",
            "scale",
            "Label",
            "text",
            "Mode",
            "Fast",
            "Slow",
            "measure",
            "width",
            "tall",
            "rest",
            "first",
            "second",
            "Tools",
            "run",
        ]

    def test_javascript_fields_are_members(self) -> None:
        source = "class Box { size = 1; grow() {} }\n"
        found = typescript.declarations(parse("m.js", source))
        assert [(item.name, item.kind, item.owner) for item in found] == [
            ("Box", "class", None),
            ("size", "field", "Box"),
            ("grow", "method", "Box"),
        ]


if __name__ == "__main__":
    unittest.main()
