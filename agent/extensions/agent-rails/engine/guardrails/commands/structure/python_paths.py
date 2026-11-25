from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import config_section, path_prefixes
from guardrails.toolkit.python_rules import path_names, scope as python_scope


def python_path_findings(
    path: str, config: dict[str, Any], existing_paths: Iterable[str]
) -> list[Finding]:
    if "python" not in config_section(config, "checkers"):
        return []
    options = python_scope.load_settings(config)
    if python_scope.is_excluded(path, options):
        return []
    segments = path_prefixes(existing_paths)
    if path.endswith(".py"):
        return path_names.structure_findings(path, options, segments)
    return path_names.skill_findings(path, options, segments)
