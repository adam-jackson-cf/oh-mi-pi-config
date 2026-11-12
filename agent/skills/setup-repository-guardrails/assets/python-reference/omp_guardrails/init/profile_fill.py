"""Write the record's ``profile.md`` from the template without overwriting recorded values.

The template is the test-guidance skill's ``assets/templates/test-profile.md``, read from the
harness (``omp_guardrails.harness.PROFILE_TEMPLATE``).

Merge rule: a key the profile already holds keeps its value. Missing keys come from the
template, recursively through nested sections. Two kinds of template object are taken whole
and never merged into an existing value:

- a map whose template keys are placeholders (``{"<package>": "<entry>"}``), because adding a
  placeholder key to a real map would corrupt it;
- any section the profile records as an exemption (``{"none": ..., "approval": ...}``).

``house_style.version`` is the one value init knows for certain, so it replaces a missing or
placeholder value with the installed house-style version.
"""

from __future__ import annotations

import json
from pathlib import Path

from omp_guardrails import harness
from omp_guardrails.config.house_style import load_house_style
from omp_guardrails.config.profile import (
    FENCE,
    PLACEHOLDER,
    PROFILE_FILE,
    PROFILE_PATH,
    ProfileError,
    parse_front_matter,
)


def template_text() -> str:
    try:
        return harness.PROFILE_TEMPLATE.read_text(encoding="utf-8")
    except OSError as error:
        raise ProfileError(
            f"cannot read the test-profile template {harness.PROFILE_TEMPLATE}: {error}"
        ) from error


def split_document(text: str) -> tuple[dict, str]:
    """Return the front-matter values and the text after the closing fence."""
    values = parse_front_matter(text)
    lines = text.splitlines(keepends=True)
    end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == FENCE)
    return values, "".join(lines[end + 1 :])


def render(values: dict, body: str) -> str:
    front = json.dumps(values, indent=2, ensure_ascii=False)
    return f"{FENCE}\n{front}\n{FENCE}\n{body}"


def is_placeholder(value: object) -> bool:
    return isinstance(value, str) and bool(PLACEHOLDER.search(value))


def is_whole_value(default: dict) -> bool:
    return any(is_placeholder(key) for key in default)


def fill_missing(existing: object, default: object) -> object:
    """Existing values win; missing keys come from the default, section by section."""
    if not isinstance(existing, dict) or not isinstance(default, dict):
        return existing
    if "none" in existing or is_whole_value(default):
        return existing
    merged = dict(existing)
    for key, value in default.items():
        merged[key] = fill_missing(existing[key], value) if key in existing else value
    return merged


def with_house_style_version(values: dict) -> dict:
    house_style = values.get("house_style")
    if not isinstance(house_style, dict):
        return values
    version = house_style.get("version")
    if isinstance(version, int) and not isinstance(version, bool):
        return values
    if version is not None and not is_placeholder(version):
        return values
    shipped = load_house_style()["version"]
    return {**values, "house_style": {**house_style, "version": shipped}}


def with_lsp_notes(values: dict, notes: list[dict]) -> tuple[dict, str | None]:
    """Add ``command_notes.lsp`` when the profile has no LSP note yet."""
    if not notes:
        return values, None
    command_notes = values.get("command_notes")
    if command_notes is None:
        return {**values, "command_notes": {"lsp": notes}}, None
    if not isinstance(command_notes, dict):
        return values, "command_notes is not an object; record the LSP server there by hand"
    if "lsp" in command_notes:
        return values, None
    return {**values, "command_notes": {**command_notes, "lsp": notes}}, None


def read_existing(record: Path) -> tuple[dict, str] | None:
    path = record / PROFILE_FILE
    if not path.exists():
        return None
    try:
        return split_document(path.read_text(encoding="utf-8"))
    except ProfileError as error:
        raise ProfileError(f"{PROFILE_PATH}: {error}") from error


def planned_profile(
    record: Path, settings: dict, lsp_notes: list[dict]
) -> tuple[str | None, list[str]]:
    """Return the new profile text (None when unchanged) and notes for the report."""
    template_values, template_body = split_document(template_text())
    existing = read_existing(record)
    current, body = existing if existing else ({}, template_body)
    values = fill_missing(current, template_values)
    assert isinstance(values, dict)
    if settings:
        values["guardrails"] = fill_missing(values.get("guardrails", {}), settings)
    values = with_house_style_version(values)
    values, note = with_lsp_notes(values, lsp_notes)
    notes = [note] if note else []
    if existing and values == current:
        return None, notes
    return render(values, body), notes


def load_settings(path: Path | None) -> dict:
    """Per-checker settings for ``guardrails`` from a JSON file (``--settings``)."""
    if path is None:
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProfileError(f"cannot read settings file {path}: {error}") from error
    if not isinstance(settings, dict):
        raise ProfileError(f"settings file {path} must hold one JSON object")
    return settings
