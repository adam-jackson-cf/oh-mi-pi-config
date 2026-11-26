from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.foundation.source import modules, python
from guardrails.foundation.source.modules import TsConfig
from guardrails.foundation.source.tree import SourceError, parse

pytestmark = pytest.mark.fast


def edges(source: str, path: str, package: str = "pkg") -> list[str]:
    module = modules.python_module(path, package)
    return modules.python_edges(python.imports(parse("m.py", source)), module, package)


class PythonEdgeTests(unittest.TestCase):
    def test_absolute_imports_inside_the_package(self) -> None:
        source = "import os\nimport pkg.a.b\nfrom pkg.c import d, e\nfrom pkg import *\nimport pkgextra\n"
        assert edges(source, "x.py") == ["pkg.a.b", "pkg.c.d", "pkg.c.e", "pkg"]

    def test_relative_imports_resolve_against_the_importer_package(self) -> None:
        source = "from . import a\nfrom ..b import c\nfrom .d import *\n"
        assert edges(source, "sub/mod.py") == ["pkg.sub.a", "pkg.b.c", "pkg.sub.d"]

    def test_a_package_init_resolves_against_itself(self) -> None:
        assert edges("from . import a\n", "sub/__init__.py") == ["pkg.sub.a"]

    def test_relative_imports_beyond_the_top_level_are_errors(self) -> None:
        with pytest.raises(SourceError, match="line 2: attempted relative import"):
            edges("import os\nfrom ... import a\n", "mod.py")


class PythonModuleNamingTests(unittest.TestCase):
    def test_a_file_is_named_by_its_path_under_the_import_root(self) -> None:
        assert modules.python_module("shop/cart.py") == modules.PythonModule(
            "shop.cart", is_package=False
        )
        assert modules.python_module("cart.py", "shop").name == "shop.cart"

    def test_a_package_marker_is_the_package_itself(self) -> None:
        found = modules.python_module("shop/__init__.py")
        assert found == modules.PythonModule("shop", is_package=True)
        assert found.package == "shop"
        assert modules.python_module("shop/cart.py").package == "shop"
        assert modules.python_module("__init__.py", "shop").name == "shop"

    def test_a_stub_names_the_module_it_describes(self) -> None:
        assert modules.python_module("shop/cart.pyi").name == "shop.cart"
        assert modules.python_module("shop/__init__.pyi").name == "shop"


def absolute(source: str, path: str) -> list[str]:
    importer = modules.python_module(path)
    return [
        modules.absolute(item, importer)
        for item in python.imports(parse("m.py", source))
    ]


class RelativeImportTests(unittest.TestCase):
    def test_a_module_resolves_against_its_package_and_a_package_against_itself(
        self,
    ) -> None:
        source = "from . import a\nfrom ..b import c\nimport os\n"
        assert absolute(source, "pkg/sub/mod.py") == ["pkg.sub", "pkg.b", "os"]
        assert absolute(source, "pkg/sub/__init__.py") == ["pkg.sub", "pkg.b", "os"]

    def test_climbing_above_the_top_package_or_leaving_no_package_is_an_error(
        self,
    ) -> None:
        with pytest.raises(SourceError, match="beyond top-level package") as raised:
            absolute("from ... import a\n", "pkg/mod.py")
        assert raised.value.line == 1
        with pytest.raises(SourceError, match="no package specified"):
            absolute("from . import a\n", "mod.py")

    def test_a_from_import_binds_each_name_under_its_module(self) -> None:
        (item,) = python.imports(parse("m.py", "from .core import run as go, stop\n"))
        importer = modules.python_module("pkg/cli.py")
        assert modules.imported_names(item, importer) == [
            "pkg.core.run",
            "pkg.core.stop",
        ]
        assert item.bindings == ("go", "stop")


def resolved(files: dict[str, str], importer: str) -> list[list[str]]:
    index = modules.PythonIndex(files)
    return [
        index.resolve(importer, item)
        for item in python.imports(parse(importer, files[importer]))
    ]


class PythonIndexTests(unittest.TestCase):
    def test_files_are_named_by_every_path_suffix(self) -> None:
        index = modules.PythonIndex([
            "src/shop/__init__.py",
            "src/shop/cart.py",
            "src/shop/sub/__init__.py",
            "src/shop/sub/cart.py",
            "scripts/tool.py",
            "README.md",
        ])
        assert index.names["src/shop/cart.py"] == ["src.shop.cart", "shop.cart"]
        assert index.names["src/shop/sub/cart.py"] == [
            "src.shop.sub.cart",
            "shop.sub.cart",
            "sub.cart",
        ]
        assert index.names["src/shop/__init__.py"] == ["src.shop", "shop"]
        assert index.names["scripts/tool.py"] == ["scripts.tool", "tool"]
        assert "README.md" not in index.names

    def test_imports_name_modules_submodules_and_the_module_holding_a_name(
        self,
    ) -> None:
        files = {
            "src/shop/__init__.py": "",
            "src/shop/cart.py": "",
            "src/shop/tax.py": "",
            "src/shop/sub/__init__.py": "",
            "src/shop/sub/run.py": (
                "from .. import cart\nfrom ..tax import rate\nfrom ... import x\n"
                "import shop.cart\nimport cart\nfrom shop import missing\n"
            ),
        }
        assert resolved(files, "src/shop/sub/run.py") == [
            ["src/shop/cart.py"],
            ["src/shop/tax.py"],
            [],
            ["src/shop/cart.py"],
            [],
            ["src/shop/__init__.py"],
        ]

    def test_a_stub_is_the_target_only_without_its_module(self) -> None:
        files = {
            "app/fast.pyi": "",
            "app/cart.py": "",
            "app/cart.pyi": "",
            "app/main.py": "import fast\nimport cart\n",
        }
        assert resolved(files, "app/main.py") == [["app/fast.pyi"], ["app/cart.py"]]

    def test_a_package_folder_used_as_an_import_root_resolves(self) -> None:
        files = {
            "shared/__init__.py": "",
            "shared/core/__init__.py": "",
            "shared/core/base.py": "",
            "shared/tools/run.py": "from core.base import Thing\nimport base\n",
        }
        assert resolved(files, "shared/tools/run.py") == [["shared/core/base.py"], []]

    def test_the_nearest_of_several_same_named_modules_wins(self) -> None:
        files = {
            "a/helpers.py": "",
            "b/helpers.py": "",
            "a/main.py": "import helpers\n",
        }
        assert resolved(files, "a/main.py") == [["a/helpers.py"]]


TRACKED = frozenset({
    "tsconfig.json",
    "web/tsconfig.json",
    "web/src/app.ts",
    "web/src/util.ts",
    "web/src/view.tsx",
    "web/src/legacy.mts",
    "web/src/widgets/index.ts",
    "web/src/data.json",
    "web/lib/shared/core.ts",
    "web/lib/exact.ts",
    "web/vendor/thing.js",
    "web/types/models.ts",
    "server/main.ts",
})
WEB = TsConfig(
    "web",
    "web",
    (
        ("@lib/*", ("lib/*", "vendor/*")),
        ("@lib/shared/*", ("lib/shared/*",)),
        ("exact", ("lib/exact.ts",)),
    ),
)


def resolve(specifier: str, config: TsConfig | None = WEB) -> str | None:
    return modules.resolve_specifier("web/src/app.ts", specifier, TRACKED, config)


class TypeScriptResolutionTests(unittest.TestCase):
    def test_relative_specifiers(self) -> None:
        assert resolve("./util") == "web/src/util.ts"
        assert resolve("./util.js") == "web/src/util.ts"
        assert resolve("./view.js") == "web/src/view.tsx"
        assert resolve("./view.jsx") == "web/src/view.tsx"
        assert resolve("./legacy.mjs") == "web/src/legacy.mts"
        assert resolve("./widgets") == "web/src/widgets/index.ts"
        assert resolve("./data.json") == "web/src/data.json"
        assert resolve("../../server/main") == "server/main.ts"
        assert resolve("./missing") is None
        assert resolve("../../../outside") is None

    def test_paths_patterns_use_the_longest_prefix_then_their_targets(self) -> None:
        assert resolve("@lib/shared/core") == "web/lib/shared/core.ts"
        assert resolve("@lib/exact") == "web/lib/exact.ts"
        assert resolve("@lib/thing") == "web/vendor/thing.js"
        assert resolve("exact") == "web/lib/exact.ts"

    def test_base_url_resolves_bare_specifiers_in_the_repository(self) -> None:
        assert resolve("types/models") == "web/types/models.ts"
        assert resolve("src/util.js") == "web/src/util.ts"

    def test_bare_package_specifiers_are_third_party(self) -> None:
        assert resolve("react") is None
        assert resolve("@scope/pkg") is None
        assert resolve("types/models", None) is None

    def test_paths_without_base_url_are_relative_to_the_config_folder(self) -> None:
        config = TsConfig("web", None, (("~/*", ("src/*",)),))
        assert resolve("~/util", config) == "web/src/util.ts"
        assert resolve("src/util", config) is None

    def test_the_nearest_config_at_or_above_the_importer(self) -> None:
        assert modules.nearest("web/src/app.ts", "tsconfig.json", TRACKED) == (
            "web/tsconfig.json"
        )
        assert modules.nearest("server/main.ts", "tsconfig.json", TRACKED) == (
            "tsconfig.json"
        )
        assert modules.nearest("server/main.ts", "go.mod", TRACKED) is None

    def test_tsconfig_reads_base_url_and_paths_from_commented_json(self) -> None:
        text = """{
  // compiler settings
  "compilerOptions": {
    "baseUrl": "./src", /* relative */
    "paths": {"@/*": ["./*", "gen/*",],},
    "outDir": "dist//keep",
  },
}"""
        assert modules.tsconfig("web/tsconfig.json", text) == TsConfig(
            "web", "web/src", (("@/*", ("./*", "gen/*")),)
        )
        assert modules.tsconfig("tsconfig.json", "{}") == TsConfig(".", None, ())

    def test_invalid_tsconfig_files_are_errors(self) -> None:
        cases = {
            "{": "tsconfig.json",
            "[]": "compilerOptions must be an object",
            '{"compilerOptions": {"baseUrl": 1}}': "baseUrl must be a string",
            '{"compilerOptions": {"paths": {"a": "b"}}}': "paths must map",
        }
        for text, message in cases.items():
            with self.subTest(text=text), pytest.raises(SourceError, match=message):
                modules.tsconfig("tsconfig.json", text)


class ScriptResolverTests(unittest.TestCase):
    def test_each_importer_reads_its_nearest_tsconfig_once(self) -> None:
        files = {
            "tsconfig.json": '{"compilerOptions": {"baseUrl": "."}}',
            "web/tsconfig.json": '{"compilerOptions": {"paths": {"@/*": ["src/*"]}}}',
            "web/src/util.ts": "",
            "web/src/app.ts": "",
            "lib/core.ts": "",
            "server/main.ts": "",
        }
        reads: list[str] = []

        def read(path: str) -> str:
            reads.append(path)
            return files[path]

        scripts = modules.ScriptResolver(set(files), read)
        assert scripts.resolve("web/src/app.ts", "@/util") == "web/src/util.ts"
        assert scripts.resolve("web/src/app.ts", "./util.js") == "web/src/util.ts"
        assert scripts.resolve("server/main.ts", "lib/core") == "lib/core.ts"
        assert scripts.resolve("web/src/app.ts", "lib/core") is None
        assert scripts.resolve("web/src/app.ts", "react") is None
        assert sorted(reads) == ["tsconfig.json", "web/tsconfig.json"]


class GoModuleTests(unittest.TestCase):
    def test_module_path_from_go_mod(self) -> None:
        assert modules.go_module_path("// c\nmodule example.com/app\n\ngo 1.22\n") == (
            "example.com/app"
        )
        assert modules.go_module_path('module "example.com/q" // note\n') == (
            "example.com/q"
        )
        with pytest.raises(SourceError, match="no module directive"):
            modules.go_module_path("go 1.22\n")

    def test_package_folders_map_to_import_paths_and_back(self) -> None:
        cases = [
            (".", ".", "example.com/app"),
            (".", "internal/store", "example.com/app/internal/store"),
            ("svc", "svc", "example.com/app"),
            ("svc", "svc/cmd/run", "example.com/app/cmd/run"),
        ]
        for module_folder, folder, import_path in cases:
            with self.subTest(folder=folder):
                assert (
                    modules.package_import_path(
                        "example.com/app", module_folder, folder
                    )
                    == import_path
                )
                assert (
                    modules.package_folder(
                        "example.com/app", module_folder, import_path
                    )
                    == folder
                )

    def test_paths_outside_the_module(self) -> None:
        assert modules.package_folder("example.com/app", ".", "fmt") is None
        assert (
            modules.package_folder("example.com/app", ".", "example.com/apple") is None
        )
        with pytest.raises(SourceError, match="outside the Go module"):
            modules.package_import_path("example.com/app", "svc", "other/pkg")


if __name__ == "__main__":
    unittest.main()
