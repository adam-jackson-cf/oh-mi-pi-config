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
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any, settings

RULE = "TEST.identity-artefacts"
CHECKER = "identity_artefacts"


def test_locations(profile: dict[str, Any]) -> list[str]:
    locations = adapter_declarations.locations(profile)
    layout = profile.get("layout")
    if isinstance(layout, dict):
        locations.extend(as_list(layout.get("support_dir")))
        locations.extend(as_list(layout.get("fixtures_dir")))
    return locations


def is_production(path: str, profile: dict[str, Any], artefacts: list[str]) -> bool:
    non_production = [
        *artefacts,
        *test_locations(profile),
        *as_list(settings(profile, CHECKER).get("non_production")),
    ]
    return not (matches_any(path, non_production) or is_test_file(path, profile))


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    declared = profile.get("identity_artefacts")
    if not isinstance(declared, dict) or "none" in declared:
        return []
    artefacts = [key for key in declared if isinstance(key, str)]
    try:
        changes = changed_files(repo, parse_range(argv))
    except DiffError as error:
        return [range_finding(RULE, error)]
    paths = [path for change in changes for path in change.paths]
    if any(is_production(path, profile, artefacts) for path in paths):
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
