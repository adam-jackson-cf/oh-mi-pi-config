from __future__ import annotations

from typing import Any

from guardrails.context.config import adapter_declarations
from guardrails.foundation.inventory import (
    as_list,
    matches_any,
    name_matches,
    tests_settings,
)

# One adapter's test files: its tier locations and the configured test file patterns.
Suite = tuple[list[str], list[str]]


def test_patterns(config: dict[str, Any]) -> list[str]:
    layout = tests_settings(config, "layout")
    patterns = (
        as_list(layout.get("test_file_patterns")) if isinstance(layout, dict) else []
    )
    return patterns


def is_test_file(path: str, config: dict[str, Any]) -> bool:
    return any(name_matches(path, pattern) for pattern in test_patterns(config))


def adapter_suite(config: dict[str, Any], name: str) -> Suite:
    return adapter_declarations.locations(config, name), test_patterns(config)


def in_suite(path: str, suite: Suite) -> bool:
    locations, patterns = suite
    return matches_any(path, locations) or any(
        name_matches(path, pattern) for pattern in patterns
    )
