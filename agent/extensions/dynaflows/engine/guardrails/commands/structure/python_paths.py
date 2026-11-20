from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import path_prefixes
from guardrails.toolkit.python_rules import path_names, scope as python_scope


def python_path_findings(
    path: str, profile: dict[str, Any], existing_paths: Iterable[str]
) -> list[Finding]:
    guardrails = profile.get("guardrails")
    if not isinstance(guardrails, dict) or "python" not in guardrails:
        return []
    config = python_scope.load_settings(profile)
    if python_scope.is_excluded(path, config):
        return []
    segments = path_prefixes(existing_paths)
    if path.endswith(".py"):
        return path_names.structure_findings(path, config, segments)
    return path_names.skill_findings(path, config, segments)
