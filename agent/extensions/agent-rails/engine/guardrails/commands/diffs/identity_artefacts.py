from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.context.changes import (
    DiffError,
    changed_files,
    parse_range,
    range_finding,
)
from guardrails.context.config import adapter_declarations
from guardrails.context.config.section_schema import approved_exemption
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    as_list,
    matches_any,
    settings,
    tests_settings,
)

RULE = "TEST.identity-artefacts"
CHECKER = "identity_artefacts"


def test_locations(config: dict[str, Any]) -> list[str]:
    locations = adapter_declarations.locations(config)
    layout = tests_settings(config, "layout")
    if isinstance(layout, dict):
        locations.extend(as_list(layout.get("support_dir")))
        locations.extend(as_list(layout.get("fixtures_dir")))
    return locations


def is_production(path: str, config: dict[str, Any], artefacts: list[str]) -> bool:
    non_production = [
        *artefacts,
        *test_locations(config),
        *as_list(settings(config, CHECKER).get("non_production")),
    ]
    return not (matches_any(path, non_production) or is_test_file(path, config))


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    declared = tests_settings(config, "identity_artefacts")
    if not isinstance(declared, dict) or approved_exemption(
        config, "identity_artefacts"
    ):
        return []
    artefacts = [key for key in declared if isinstance(key, str) and key != "none"]
    try:
        changes = changed_files(repo, parse_range(argv))
    except DiffError as error:
        return [range_finding(RULE, error)]
    paths = [path for change in changes for path in change.paths]
    if any(is_production(path, config, artefacts) for path in paths):
        return []
    return [
        Finding(
            RULE,
            path,
            "identity-bearing artefact changed in test-only work "
            f"(rule: {declared[next(key for key in artefacts if matches_any(path, [key]))]})",
            "restore the artefact, or make the change in work that also changes production code",
        )
        for path in sorted(set(paths))
        if matches_any(path, artefacts)
    ]
