from __future__ import annotations

from typing import Any

from guardrails.foundation import conventions
from guardrails.foundation.inventory import as_list, name_matches


def test_patterns(profile: dict[str, Any]) -> list[str]:
    layout = profile.get("layout")
    patterns = (
        as_list(layout.get("test_file_patterns")) if isinstance(layout, dict) else []
    )
    if patterns:
        return patterns
    return conventions.all_test_patterns()


def is_test_file(path: str, profile: dict[str, Any]) -> bool:
    return any(name_matches(path, pattern) for pattern in test_patterns(profile))
