"""Readers for the sections of a loaded .agentrails/config.json; validator.py checks the file."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def config_section(config: Mapping[str, Any], name: str) -> dict[str, Any]:
    """One top-level section of the repo's .agentrails/config.json, {} when absent."""
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def checker_settings(config: dict[str, Any], checker: str) -> dict[str, Any]:
    """checkers.<checker>: one checker's settings."""
    value = config_section(config, "checkers").get(checker)
    return value if isinstance(value, dict) else {}


def tests_settings(config: dict[str, Any], key: str) -> Any:
    """tests.<key>: the test-guidance settings (tiers, adapters, layout, ...)."""
    return config_section(config, "tests").get(key)


def structure_settings(config: dict[str, Any]) -> dict[str, Any]:
    """structure: owners, tiers, import_direction and ownership_areas."""
    return config_section(config, "structure")


def config_exceptions(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The owner-approved exceptions: {rule, path, check?, reason, approvedBy, approvedAt}."""
    entries = config.get("exceptions")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def approvals(config: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """The owner approvals of one kind (budget, ratchet, skip or exemption)."""
    entries = config.get("approvals")
    return [
        entry
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict) and entry.get("kind") == kind
    ]


def approved_targets(config: dict[str, Any], kind: str) -> set[str]:
    """The targets of one kind's approvals that carry an approver and a reason."""
    return {
        entry["target"]
        for entry in approvals(config, kind)
        if isinstance(entry.get("target"), str)
        and entry["target"].strip()
        and all(
            isinstance(entry.get(key), str) and entry[key].strip()
            for key in ("reason", "approvedBy")
        )
    }
