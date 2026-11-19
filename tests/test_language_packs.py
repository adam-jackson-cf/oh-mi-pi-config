from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands.structure import naming
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation import conventions

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]


class LanguagePackTests(unittest.TestCase):
    def test_fixed_ecosystem_and_tool_names_need_no_record_setting(self) -> None:
        paths = (
            "init/__init__.py",
            "main/__main__.py",
            "conftest/conftest.py",
            "clock/clock.go",
            "clock/clock_test.go",
            "doc/doc.go",
            "main/main.go",
            "index/index.ts",
            "schema/schema.d.ts",
            ".devcontainer/devcontainer.json",
            "Dockerfile/Dockerfile",
            "Makefile/Makefile",
            "package/package.json",
            "pyproject/pyproject.toml",
            "go/go.mod",
            "AGENTS/AGENTS.md",
            "CLAUDE/CLAUDE.md",
        )
        with tempfile.TemporaryDirectory() as directory:
            for path in paths:
                with self.subTest(path=path):
                    assert naming.path_findings(Path(directory), path, {}) == []

    def test_extension_selects_python_without_record_ecosystems(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            found = naming.path_findings(Path(directory), "account/BadName.py", {})
        assert any("not snake_case" in item.message for item in found)

    def test_javascript_and_typescript_do_not_impose_personal_casing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for path in (
                "UI/AccountView.tsx",
                "frontend/account-view.js",
                "frontend/account_view.mjs",
            ):
                with self.subTest(path=path):
                    assert naming.path_findings(Path(directory), path, {}) == []

    def test_ecosystem_discovery_and_placement_are_automatic(self) -> None:
        for path in (
            "account/test_balance.py",
            "account/balance_test.py",
            "account/balance_test.go",
            "account/balance.spec.ts",
            "account/balance.test.jsx",
            "account/__tests__/balance.ts",
        ):
            with self.subTest(path=path):
                assert is_test_file(path, {})
                assert conventions.placement(path)["beside_code"]
        assert conventions.fixed_folder("account/check.ts", "__tests__")
        assert conventions.fixed_folder("clock/read.go", "testdata")

    def test_installed_dependency_names_are_not_judged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for path in ("node_modules/common/BadName.ts", "vendor/common/BadName.go"):
                assert naming.path_findings(Path(directory), path, {}) == []

    def test_nonfixed_file_stems_still_cannot_repeat_parent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for path in (
                "clock/clock_reader.go",
                "account/account-view.ts",
                "account/account.sh",
            ):
                found = naming.path_findings(Path(directory), path, {})
                assert any(item.check == "child-repeats-parent" for item in found), path

    def test_pre_event_inventory_judges_only_added_segments(self) -> None:
        baseline = {"common/BadFolder/common_parser.py"}
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            assert (
                naming.path_findings(
                    repo, next(iter(baseline)), {}, existing_paths=baseline
                )
                == []
            )
            assert (
                naming.path_findings(
                    repo, "common/BadFolder/reader.py", {}, existing_paths=baseline
                )
                == []
            )
            found = naming.path_findings(
                repo, "common/BadFolder/helpers/reader.py", {}, existing_paths=baseline
            )
            assert any("helpers" in item.message for item in found)
            repeated = naming.path_findings(
                repo,
                "common/BadFolder/bad_folder_reader.py",
                {},
                existing_paths=baseline,
            )
            assert any(item.check == "child-repeats-parent" for item in repeated)
            assert naming.path_findings(repo, next(iter(baseline)), {})

    def test_code_versions_fail_but_data_version_paths_remain_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for suffix in ("py", "ts", "js", "go", "sh"):
                found = naming.path_findings(repo, f"account/v2.{suffix}", {})
                assert any("version segment" in item.message for item in found)
            assert naming.path_findings(repo, "account/v2/snapshot.json", {}) == []
            assert naming.path_findings(repo, "account/v2.json", {}) == []

    def test_every_fixed_name_cites_its_tool_source(self) -> None:
        for pack in conventions.packs():
            sources = pack["convention_sources"].get("fixed_files", {})
            for name in pack["fixed_files"]:
                with self.subTest(pack=pack["name"], name=name):
                    assert sources.get(name, "").startswith("https://")

    def test_lockfile_and_tool_config_names_are_fixed(self) -> None:
        paths = (
            "uv/uv.lock",
            "bun/bun.lock",
            "pnpm/pnpm-lock.yaml",
            "yarn/yarn.lock",
            "go/go.work",
            "go/go.work.sum",
            "requirements/requirements.txt",
            "ruff/ruff.toml",
            "mypy/mypy.ini",
            "golangci/.golangci.yml",
            "shellcheckrc/.shellcheckrc",
            "biome/biome.json",
            "eslint/eslint.config.mjs",
            "oxlint/oxlint.config.ts",
            "oxlintrc/.oxlintrc.json",
            "vitest/vitest.config.ts",
            "importlinter/.importlinter",
        )
        with tempfile.TemporaryDirectory() as directory:
            for path in paths:
                with self.subTest(path=path):
                    assert conventions.fixed_file(path)
                    assert naming.path_findings(Path(directory), path, {}) == []

    def test_python_directory_casing_applies_only_to_packages(self) -> None:
        def directory_casing(repo: Path) -> list[str]:
            found = naming.path_findings(repo, "fixed-plan-effort/aggregate.py", {})
            return [item.message for item in found if "directory" in item.message]

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "fixed-plan-effort").mkdir()
            assert directory_casing(repo) == []
            (repo / "fixed-plan-effort" / "__init__.py").write_text(
                "", encoding="utf-8"
            )
            assert directory_casing(repo) == [
                "python directory 'fixed-plan-effort' is not snake_case"
            ]
        sources = conventions.load_pack("python")["convention_sources"]
        assert sources["package_marker"]["__init__.py"].startswith(
            "https://docs.python.org/3/reference/import.html"
        )
        assert sources["directory"]["snake_case"].startswith(
            "https://peps.python.org/pep-0008/"
        )

    def test_http_server_handler_methods_are_framework_symbols(self) -> None:
        source = (
            "from http.server import BaseHTTPRequestHandler\n\n\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            "    def do_POST(self):\n        pass\n\n"
            "    def doPost(self):\n        pass\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "handler.py").write_text(source, encoding="utf-8")
            found = naming.path_findings(repo, "handler.py", {})
        symbols = [item.message for item in found if "symbol" in item.message]
        assert symbols == ["line 8: symbol 'doPost' is not snake_case"]
        sources = conventions.load_pack("python")["convention_sources"][
            "framework_symbols"
        ]
        assert sources["^do_[A-Z]+$"].startswith(
            "https://docs.python.org/3/library/http.server.html"
        )

    def test_every_pack_cites_upstream_conventions(self) -> None:
        for pack in conventions.packs():
            assert pack["sources"]
            assert all(url.startswith("https://") for url in pack["sources"])
            assert "file" in pack
            assert "directory" in pack
            assert "placement" in pack
