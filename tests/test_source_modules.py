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


def edges(source: str, module: str, package: str = "pkg") -> list[str]:
    return modules.python_edges(python.imports(parse("m.py", source)), module, package)


class PythonEdgeTests(unittest.TestCase):
    def test_absolute_imports_inside_the_package(self) -> None:
        source = "import os\nimport pkg.a.b\nfrom pkg.c import d, e\nfrom pkg import *\nimport pkgextra\n"
        assert edges(source, "pkg.x") == ["pkg.a.b", "pkg.c.d", "pkg.c.e", "pkg"]

    def test_relative_imports_resolve_against_the_importer_package(self) -> None:
        source = "from . import a\nfrom ..b import c\nfrom .d import *\n"
        assert edges(source, "pkg.sub.mod") == ["pkg.sub.a", "pkg.b.c", "pkg.sub.d"]

    def test_a_package_init_resolves_against_itself(self) -> None:
        assert edges("from . import a\n", "pkg.sub.__init__") == ["pkg.sub.a"]

    def test_relative_imports_beyond_the_top_level_are_errors(self) -> None:
        with pytest.raises(SourceError, match="line 2: attempted relative import"):
            edges("import os\nfrom ... import a\n", "pkg.mod")


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
