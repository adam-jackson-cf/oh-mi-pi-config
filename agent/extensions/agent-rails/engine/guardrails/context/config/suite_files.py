from __future__ import annotations

from typing import Any

from guardrails.foundation.inventory import as_list, name_matches, tests_settings


def test_patterns(config: dict[str, Any]) -> list[str]:
    layout = tests_settings(config, "layout")
    patterns = (
        as_list(layout.get("test_file_patterns")) if isinstance(layout, dict) else []
    )
    return patterns


def is_test_file(path: str, config: dict[str, Any]) -> bool:
    return any(name_matches(path, pattern) for pattern in test_patterns(config))
