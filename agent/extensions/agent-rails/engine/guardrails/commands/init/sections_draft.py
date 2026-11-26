"""Draft the setup-owned sections of a repo config for `guardrails init --out`.

The draft starts from the repo's own config sections when it has a valid config, otherwise
from the engine's section template (data/config-template.json). Checker settings passed
with --settings fill only the keys a checker section lacks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from guardrails.context.config.house_style import load_house_style
from guardrails.context.config.section_schema import PLACEHOLDER, ConfigValueError
from guardrails.context.config.validator import validate_section

DATA_FOLDER = Path(__file__).resolve().parents[3] / "data"
SETUP_SECTIONS = ("commands", "checkers", "structure", "houseStyle", "tests")
TEMPLATE = "config-template.json"


def template() -> dict[str, Any]:
    text = (DATA_FOLDER / TEMPLATE).read_text("utf-8")
    loaded: dict[str, Any] = json.loads(text)
    return {name: loaded[name] for name in SETUP_SECTIONS}


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER.search(value))


def is_whole_value(default: dict[str, Any]) -> bool:
    return any(is_placeholder(key) for key in default)


def fill_missing(existing: object, default: object) -> object:
    if not isinstance(existing, dict) or not isinstance(default, dict):
        return existing
    if "none" in existing or is_whole_value(default):
        return existing
    merged = dict(existing)
    for key, value in default.items():
        merged[key] = fill_missing(existing[key], value) if key in existing else value
    return merged


def with_house_style_version(house_style: dict[str, Any]) -> dict[str, Any]:
    version = house_style.get("version")
    if isinstance(version, int) and not isinstance(version, bool):
        return house_style
    if version is not None and not is_placeholder(version):
        return house_style
    return {**house_style, "version": load_house_style()["version"]}


def with_lsp_notes(
    tests: dict[str, Any], notes: list[dict[str, Any]]
) -> tuple[dict[str, Any], str | None]:
    if not notes:
        return tests, None
    command_notes = tests.get("command_notes")
    if command_notes is None:
        return {**tests, "command_notes": {"lsp": notes}}, None
    if not isinstance(command_notes, dict):
        return (
            tests,
            "tests.command_notes is not an object; write the LSP server there by hand",
        )
    if "lsp" in command_notes:
        return tests, None
    return {**tests, "command_notes": {**command_notes, "lsp": notes}}, None


def drafted_sections(
    base: dict[str, Any], settings: dict[str, Any], lsp_notes: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """The five setup sections: base (a valid config's sections) or the template's, then
    checker settings and LSP notes."""
    defaults = template()
    values = {name: base.get(name, defaults[name]) for name in SETUP_SECTIONS}
    if settings:
        values["checkers"] = fill_missing(values.get("checkers", {}), settings)
    values["houseStyle"] = with_house_style_version(values["houseStyle"])
    values["tests"], note = with_lsp_notes(values["tests"], lsp_notes)
    return {name: values[name] for name in SETUP_SECTIONS}, [note] if note else []


def section_problems(sections: dict[str, Any]) -> list[str]:
    """What still needs a real value before each section validates."""
    return [
        problem
        for name in SETUP_SECTIONS
        for problem in validate_section(name, sections.get(name))
    ]


def render(sections: dict[str, Any]) -> str:
    return json.dumps(sections, indent=2, ensure_ascii=False) + "\n"


def load_settings(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigValueError(f"cannot read settings file {path}: {error}") from error
    if not isinstance(settings, dict):
        raise ConfigValueError(f"settings file {path} must hold one JSON object")
    return settings
