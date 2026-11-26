"""Committed package layouts for the runtime-data and entry-point placement checks."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands.structure_naming import (
    go_placement,
    layout,
    package_placement,
    script_placement,
)
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding, catalogue_rules, remediations
from guardrails.foundation.source.tree import DATA_FOLDER

pytestmark = pytest.mark.fast

FIXTURES = Path(__file__).resolve().parent / "fixtures/structure"
LANGUAGES = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "agent/extensions/agent-rails/engine/data/structure-languages.json"
    ).read_text()
)["rules"]
CASES = json.loads((FIXTURES / "package-placement.json").read_text())["cases"]


class PackagePlacementTests(unittest.TestCase):
    def test_each_layout_reports_exactly_its_misplaced_files(self) -> None:
        for case in CASES:
            with self.subTest(case=case["name"]):
                repo_config = case.get("config", {})
                found = apply_exceptions(
                    package_placement.package_file_findings(
                        case.get("paths", case["inventory"]),
                        case["inventory"],
                        repo_config,
                    ),
                    repo_config,
                )
                assert sorted((item.rule_id, item.path) for item in found) == sorted(
                    tuple(row) for row in case["findings"]
                )

    def test_findings_name_the_package_and_the_way_out(self) -> None:
        inventory = [
            "src/shop/__init__.py",
            "src/shop/rules/policy.yaml",
            "src/shop/engine/__init__.py",
            "src/shop/engine/jobs/__init__.py",
            "src/shop/engine/jobs/__main__.py",
        ]
        data, entry = package_placement.package_file_findings(
            ["src/shop/rules/policy.yaml", "src/shop/engine/jobs/__main__.py"],
            inventory,
            {},
        )
        assert data.render() == (
            "src/shop/rules/policy.yaml: STRUCT.runtime-data-placement: "
            "runtime data file inside code folder src/shop/; move it to a root data "
            "folder outside the code and read it from there"
        )
        assert entry.render() == (
            "src/shop/engine/jobs/__main__.py: STRUCT.entry-point-placement: "
            "entry module __main__.py sits 2 folders deep in package src/shop/, below "
            "its top tier; move it to src/shop/ or a folder directly under it, or record "
            "an owner-approved exceptions entry for an internal tool"
        )
        for rule in (package_placement.RUNTIME_DATA, package_placement.ENTRY_POINT):
            assert remediations()[rule].render().startswith("root cause: ")

    def test_catalogue_contracts_state_the_implemented_lists(self) -> None:
        rules = {rule["id"]: rule["contract"] for rule in catalogue_rules()}
        data = rules[package_placement.RUNTIME_DATA]
        assert data["data_suffixes"] == sorted(package_placement.DATA_SUFFIXES)
        languages = json.loads(
            (DATA_FOLDER / "structure-languages.json").read_text(encoding="utf-8")
        )
        python = languages["rules"][package_placement.ENTRY_POINT]["python"]
        assert python["entry_modules"] == sorted(package_placement.entry_modules())


class SourceRepoCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-placement-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def repo(self, files: dict[str, str]) -> list[str]:
        for path, text in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return sorted(files)

    def script(
        self, files: dict[str, str], paths: list[str] | None = None
    ) -> list[Finding]:
        inventory = self.repo(files)
        return script_placement.script_file_findings(
            self.root, inventory if paths is None else paths, inventory, {}
        )

    def go(
        self, files: dict[str, str], paths: list[str] | None = None
    ) -> list[Finding]:
        inventory = self.repo(files)
        return go_placement.go_file_findings(
            self.root, inventory if paths is None else paths, inventory, {}
        )


def pairs(found: list[Finding]) -> set[tuple[str, str]]:
    return {(item.rule_id, item.path) for item in found}


DATA = package_placement.RUNTIME_DATA
ENTRY = package_placement.ENTRY_POINT


class ScriptRuntimeDataTests(SourceRepoCase):
    def test_data_beside_code_that_does_not_import_it_is_flagged(self) -> None:
        found = self.script({
            "web/package.json": "{}",
            "web/src/app.ts": 'import { a } from "./a";\n',
            "web/src/a.ts": "export const a = 1;\n",
            "web/src/settings.yaml": "",
            "web/src/rules/limits.json": "{}",
        })
        assert pairs(found) == {
            (DATA, "web/src/settings.yaml"),
            (DATA, "web/src/rules/limits.json"),
        }
        rendered = {item.path: item.render() for item in found}
        assert rendered["web/src/settings.yaml"] == (
            "web/src/settings.yaml: STRUCT.runtime-data-placement: runtime data file "
            "inside code folder web/src/; move it to a root data folder outside the "
            "code and read it from there"
        )

    def test_json_a_same_folder_source_imports_by_relative_specifier_is_code(
        self,
    ) -> None:
        found = self.script({
            "web/src/app.ts": 'import config from "./config.json" with { type: "json" };\n',
            "web/src/config.json": "{}",
            "web/src/load.cjs": 'const table = require("./table.json");\n',
            "web/src/table.json": "{}",
            "web/src/lazy.tsx": 'export const m = import("./lazy.json");\n',
            "web/src/lazy.json": "{}",
            "web/src/view.ts": 'import data from "./data/rows.json";\n',
            "web/src/data/rows.json": "[]",
            "web/src/named.ts": 'import cfg from "config.json";\n',
        })
        assert pairs(found) == {(DATA, "web/src/data/rows.json")}

    def test_tooling_fixed_files_are_not_runtime_data(self) -> None:
        found = self.script({
            "web/src/app.ts": "",
            "web/src/package.json": "{}",
            "web/src/tsconfig.json": "{}",
            "web/src/tsconfig.build.json": "{}",
            "web/src/package-lock.json": "{}",
            "web/src/pnpm-lock.yaml": "",
        })
        assert found == []

    def test_a_bundle_manifest_ends_the_code_folder_search(self) -> None:
        found = self.script({
            "tools/index.js": "",
            "tools/kit/package.json": "{}",
            "tools/kit/assets/map.json": "{}",
            "tools/skill/SKILL.md": "",
            "tools/skill/data/terms.yaml": "",
            "tools/loose/config.toml": "",
        })
        assert pairs(found) == {(DATA, "tools/loose/config.toml")}

    def test_test_folders_hold_data_freely(self) -> None:
        found = self.script({
            "web/src/app.ts": "",
            "web/src/tests/cases.json": "{}",
            "web/src/fixtures/order.json": "{}",
        })
        assert found == []

    def test_an_unparsable_source_beside_the_data_fails_closed(self) -> None:
        found = self.script(
            {"web/src/app.ts": "const = ;\n", "web/src/config.json": "{}"},
            ["web/src/config.json"],
        )
        assert pairs(found) == {(DATA, "web/src/config.json")}
        assert "cannot parse web/src/app.ts as typescript" in found[0].message


class ScriptEntryPointTests(SourceRepoCase):
    def test_bin_and_main_in_the_top_tier_are_clean(self) -> None:
        found = self.script({
            "shop/package.json": json.dumps({
                "bin": {"shop": "./bin/shop.js"},
                "main": "src/index.ts",
            }),
            "shop/bin/shop.js": "",
            "shop/src/index.ts": "",
            "package.json": json.dumps({"main": "index.js"}),
            "index.js": "",
        })
        assert found == []

    def test_deep_bin_and_main_entries_are_flagged(self) -> None:
        found = self.script({
            "shop/package.json": json.dumps({
                "bin": "src/cli/run.ts",
                "main": "src/core/engine/index.ts",
            }),
            "shop/src/cli/run.ts": "",
            "shop/src/core/engine/index.ts": "",
        })
        assert pairs(found) == {
            (ENTRY, "shop/src/cli/run.ts"),
            (ENTRY, "shop/src/core/engine/index.ts"),
        }
        assert sorted(item.render() for item in found)[0] == (
            "shop/src/cli/run.ts: STRUCT.entry-point-placement: shop/package.json bin "
            "entry run.ts sits 2 folders below shop/, below its top tier; move it to "
            "shop/ or a folder directly under it and update bin in shop/package.json, "
            "or record an owner-approved exceptions entry for an internal tool"
        )

    def test_a_root_package_names_the_package_root(self) -> None:
        found = self.script({
            "package.json": json.dumps({"bin": {"a": "src/tools/a.mjs"}}),
            "src/tools/a.mjs": "",
        })
        assert "sits 2 folders below the package root" in found[0].message

    def test_untracked_entries_and_unchanged_paths_are_not_judged(self) -> None:
        files = {
            "shop/package.json": json.dumps({"main": "dist/lib/out/index.js"}),
            "shop/src/a/b/c.ts": "",
        }
        assert self.script(files) == []
        files["shop/package.json"] = json.dumps({"main": "src/a/b/c.ts"})
        assert self.script(files, ["shop/src/x.ts"]) == []
        assert pairs(self.script(files, ["shop/package.json"])) == {
            (ENTRY, "shop/src/a/b/c.ts")
        }

    def test_test_folders_hold_entries_freely(self) -> None:
        found = self.script({
            "tests/fixtures/pkg/package.json": json.dumps({"bin": "a/b/c.js"}),
            "tests/fixtures/pkg/a/b/c.js": "",
        })
        assert found == []

    def test_a_malformed_manifest_fails_closed(self) -> None:
        found = self.script(
            {"shop/package.json": json.dumps({"bin": 3}), "shop/a.ts": ""},
            ["shop/a.ts"],
        )
        assert pairs(found) == {(ENTRY, "shop/package.json")}
        assert "bin must be a path or a map" in found[0].message


class GoRuntimeDataTests(SourceRepoCase):
    def test_data_in_a_package_without_an_embed_is_flagged(self) -> None:
        found = self.go({
            "go.mod": "module example.com/shop\n",
            "internal/web/server.go": "package web\n",
            "internal/web/templates/index.tmpl": "",
            "internal/web/config.yaml": "",
        })
        assert pairs(found) == {
            (DATA, "internal/web/templates/index.tmpl"),
            (DATA, "internal/web/config.yaml"),
        }
        assert found[0].fix.startswith("move it to a root data folder")
        assert "code folder internal/web/" in found[0].message

    def test_files_a_same_package_embed_pattern_matches_are_code(self) -> None:
        found = self.go({
            "go.mod": "module example.com/shop\n",
            "internal/web/server.go": (
                'package web\n\nimport _ "embed"\n\n'
                "//go:embed templates config/*.yaml\nvar files string\n"
            ),
            "internal/web/templates/index.tmpl": "",
            "internal/web/templates/mail/body.tmpl": "",
            "internal/web/templates/_partial.tmpl": "",
            "internal/web/config/app.yaml": "",
            "internal/web/config/deep/app.yaml": "",
            "internal/web/other.json": "",
        })
        assert pairs(found) == {
            (DATA, "internal/web/templates/_partial.tmpl"),
            (DATA, "internal/web/config/deep/app.yaml"),
            (DATA, "internal/web/other.json"),
        }

    def test_an_all_prefix_embeds_hidden_names(self) -> None:
        found = self.go({
            "internal/web/server.go": "package web\n\n//go:embed all:templates\nvar f string\n",
            "internal/web/templates/_partial.tmpl": "",
            "internal/web/templates/.hidden.json": "",
        })
        assert found == []

    def test_testdata_is_ignored_and_go_mod_ends_the_search(self) -> None:
        found = self.go({
            "internal/web/server.go": "package web\n",
            "internal/web/testdata/golden.json": "{}",
            "internal/web/sub/testdata/deep/case.yaml": "",
            "internal/web/gen/go.mod": "module example.com/gen\n",
            "internal/web/gen/settings.toml": "",
        })
        assert found == []

    def test_an_invalid_embed_pattern_fails_closed(self) -> None:
        found = self.go({
            "internal/web/server.go": 'package web\n\n//go:embed "bad\\q"\nvar f string\n',
            "internal/web/data.json": "{}",
        })
        assert pairs(found) == {(DATA, "internal/web/data.json")}
        assert "invalid //go:embed pattern" in found[0].message


class GoEntryPointTests(SourceRepoCase):
    def test_commands_at_the_module_root_or_under_cmd_are_clean(self) -> None:
        found = self.go({
            "go.mod": "module example.com/shop\n",
            "main.go": "package main\n",
            "cmd/shop/main.go": "package main\n",
            "cmd/admin/serve.go": "package main\n",
            "tools/go.mod": "module example.com/tools\n",
            "tools/main.go": "package main\n",
            "tools/cmd/lint/main.go": "package main\n",
            "internal/store/store.go": "package store\n",
        })
        assert found == []

    def test_package_main_elsewhere_is_flagged(self) -> None:
        found = self.go({
            "go.mod": "module example.com/shop\n",
            "internal/tools/gen/main.go": "package main\n",
            "cmd/main.go": "package main\n",
            "cmd/shop/debug/main.go": "package main\n",
            "tools/go.mod": "module example.com/tools\n",
            "tools/lint/run.go": "package main\n",
        })
        assert pairs(found) == {
            (ENTRY, "internal/tools/gen/main.go"),
            (ENTRY, "cmd/main.go"),
            (ENTRY, "cmd/shop/debug/main.go"),
            (ENTRY, "tools/lint/run.go"),
        }
        rendered = {item.path: item.render() for item in found}
        assert rendered["internal/tools/gen/main.go"] == (
            "internal/tools/gen/main.go: STRUCT.entry-point-placement: package main "
            "file main.go sits in internal/tools/gen/, outside the module root ./ and "
            "cmd/<name>/; move the command to cmd/<name>/ or the module root, or record "
            "an owner-approved exceptions entry for an internal tool"
        )
        assert (
            "outside the module root tools/ and tools/cmd/<name>/"
            in (rendered["tools/lint/run.go"])
        )

    def test_tests_ignored_builds_testdata_and_moduleless_files_are_not_entries(
        self,
    ) -> None:
        found = self.go({
            "go.mod": "module example.com/shop\n",
            "internal/gen/gen.go": "//go:build ignore\n\npackage main\n",
            "internal/gen/gen_test.go": "package main\n",
            "internal/gen/testdata/prog/main.go": "package main\n",
            "tests/fixtures/prog/sub/main.go": "package main\n",
        })
        assert found == []
        assert self.go({"a/b/main.go": "package main\n"}) == []

    def test_an_unparsable_go_file_fails_closed(self) -> None:
        found = self.go({
            "go.mod": "module example.com/shop\n",
            "internal/x/x.go": "package main\nfunc (\n",
        })
        assert pairs(found) == {(ENTRY, "internal/x/x.go")}
        assert "cannot parse internal/x/x.go as go" in found[0].message


class LanguageSpecTests(SourceRepoCase):
    def test_bundle_manifests_follow_the_language_definitions(self) -> None:
        manifests = {
            language: entry["bundle_manifests"]
            for language, entry in LANGUAGES["STRUCT.runtime-data-placement"].items()
        }
        assert manifests == {
            language: sorted(package_placement.bundle_manifests(language))
            for language in ("python", "typescript", "go")
        }

    def test_tool_configs_and_bundle_manifests_beside_code_are_not_runtime_data(
        self,
    ) -> None:
        inventory = self.repo({
            "tools/x/cli.py": "",
            "tools/x/pyproject.toml": "",
            "tools/x/ruff.toml": "",
            "tools/x/setup.cfg": "",
            "tools/x/mypy.ini": "",
            "svc/main.go": "package main\n",
            "svc/.golangci.yml": "",
            "web/view.ts": "",
            "web/biome.json": "{}",
            "pkg/core.py": "",
            "pkg/rules.yaml": "",
            "vendor/lib/lib.go": "package lib\n",
            "vendor/lib/schema.json": "{}",
        })
        found = layout.package_placement_findings(self.root, inventory, inventory, {})
        assert pairs(found) == {(DATA, "pkg/rules.yaml")}

    def test_one_finding_per_file_when_languages_share_a_folder(self) -> None:
        inventory = self.repo({
            "app/main.go": "package app\n",
            "app/view.ts": "",
            "app/settings.yaml": "",
        })
        found = layout.package_placement_findings(self.root, inventory, inventory, {})
        assert pairs(found) == {(DATA, "app/settings.yaml")}


if __name__ == "__main__":
    unittest.main()
