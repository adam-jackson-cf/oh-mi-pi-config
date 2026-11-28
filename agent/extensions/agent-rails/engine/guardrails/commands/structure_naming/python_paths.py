from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from guardrails.checker.python_rules import path_names, settings as python_settings
from guardrails.context.config.reader import config_section
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import path_prefixes


def python_path_findings(
    path: str, config: dict[str, Any], existing_paths: Iterable[str]
) -> list[Finding]:
    if "python" not in config_section(config, "checkers"):
        return []
    options = python_settings.load_settings(config)
    if python_settings.is_excluded(path, options):
        return []
    segments = path_prefixes(existing_paths)
    if python_settings.python_source(path):
        return path_names.structure_findings(path, options, segments)
    return path_names.skill_findings(path, options, segments)
