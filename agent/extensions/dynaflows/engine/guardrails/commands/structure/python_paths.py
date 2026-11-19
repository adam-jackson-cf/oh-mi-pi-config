from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from guardrails.foundation.findings import Finding
from guardrails.toolkit.python_rules import path_names, scope as python_scope


def existing_segments(paths: Iterable[str]) -> set[str]:
    segments: set[str] = set()
    for item in paths:
        parts = PurePosixPath(item).parts
        segments.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return segments


def python_path_findings(
    path: str, profile: dict[str, Any], existing_paths: Iterable[str]
) -> list[Finding]:
    guardrails = profile.get("guardrails")
    if not isinstance(guardrails, dict) or "python" not in guardrails:
        return []
    config = python_scope.load_settings(profile)
    if python_scope.is_excluded(path, config):
        return []
    segments = existing_segments(existing_paths)
    if path.endswith(".py"):
        return path_names.structure_findings(path, config, segments)
    return path_names.skill_findings(path, config, segments)
