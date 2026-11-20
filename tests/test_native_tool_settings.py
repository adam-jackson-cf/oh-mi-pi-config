import json
import re
import tomllib
import unittest
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "agent/extensions/dynaflows/engine/guardrails/data/native-tools.json"


def catalogue(tool: str) -> Any:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))[tool]


def pyproject_tool(name: str) -> dict[str, Any]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    tool: dict[str, Any] = data["tool"][name]
    return tool


def section(settings: dict[str, Any], dotted: str) -> dict[str, Any]:
    for part in dotted.split("."):
        settings = settings[part]
    return settings


def json_settings(name: str) -> dict[str, Any]:
    settings: dict[str, Any] = json.loads((ROOT / name).read_text(encoding="utf-8"))
    return settings


def editorconfig_sections() -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for raw in (ROOT / ".editorconfig").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("["):
            current = line
            sections[current] = {}
        elif current:
            key, value = (part.strip() for part in line.split("=", 1))
            sections[current][key] = value
    return sections


def editorconfig_value(value: object) -> str:
    return str(value).lower() if isinstance(value, bool) else str(value)


class NativeToolSettingsTests(unittest.TestCase):
    def test_ruff_settings_match_the_catalogue(self) -> None:
        ruff = pyproject_tool("ruff")
        for name, expected in catalogue("ruff").items():
            actual = ruff if name == "root" else section(ruff, name)
            assert {key: actual.get(key) for key in expected} == expected, name

    def test_mypy_settings_match_the_catalogue(self) -> None:
        mypy = pyproject_tool("mypy")
        expected = catalogue("mypy")
        assert {key: mypy.get(key) for key in expected} == expected

    def test_tsc_settings_match_the_catalogue(self) -> None:
        options = json_settings("tsconfig.json")["compilerOptions"]
        expected = catalogue("tsc")["compilerOptions"]
        assert {key: options.get(key) for key in expected} == expected

    def test_oxlint_settings_match_the_catalogue(self) -> None:
        settings = json_settings(".oxlintrc.json")
        expected = catalogue("oxlint")
        assert settings["plugins"] == expected["plugins"]
        assert settings["categories"] == expected["categories"]
        rules, wanted = settings["rules"], expected["rules"]
        assert {rule: rules.get(rule) for rule in wanted} == wanted

    def test_shellcheck_settings_match_the_catalogue(self) -> None:
        expected = catalogue("shellcheck")
        enabled: set[str] = set()
        settings: dict[str, str] = {}
        for raw in (ROOT / ".shellcheckrc").read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key == "enable":
                enabled.update(value.split(","))
            else:
                settings[key] = value
        assert enabled == set(expected["enable"])
        assert settings == {
            "external-sources": editorconfig_value(expected["external-sources"]),
            "source-path": expected["source-path"],
        }

    def test_shfmt_settings_match_the_catalogue(self) -> None:
        expected = catalogue("shfmt")
        sections = editorconfig_sections()
        assert expected["section"] in sections
        assert sections[expected["section"]] == {
            key: editorconfig_value(value)
            for key, value in expected["settings"].items()
        }

    def test_prettier_and_markdownlint_are_pinned_exactly_once(self) -> None:
        package = json_settings("package.json")
        sections = [package.get("dependencies", {}), package.get("devDependencies", {})]
        for tool in ("prettier", "markdownlint-cli2"):
            with self.subTest(tool=tool):
                pins = [section[tool] for section in sections if tool in section]
                assert len(pins) == 1
                assert re.fullmatch(r"\d+\.\d+\.\d+", pins[0])

    def test_prettier_overrides_name_existing_files(self) -> None:
        for override in json_settings(".prettierrc.json")["overrides"]:
            for pattern in override["files"]:
                with self.subTest(pattern=pattern):
                    assert list(ROOT.glob(pattern))

    def test_markdownlint_settings_hold_the_line_length_and_name_existing_trees(
        self,
    ) -> None:
        text = (ROOT / ".markdownlint-cli2.jsonc").read_text(encoding="utf-8")
        settings = json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
        assert settings["config"]["MD013"] == {"line_length": 100}
        for override in settings["overrides"]:
            for pattern in override["filter"]:
                with self.subTest(pattern=pattern):
                    assert (ROOT / pattern.removesuffix("/**")).is_dir()
