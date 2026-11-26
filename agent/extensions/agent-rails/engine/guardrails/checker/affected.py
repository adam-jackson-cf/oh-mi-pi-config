"""The T2 test files a diff range affects, from the config's ownership areas."""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from guardrails.context.changes import (
    DiffError,
    changed_files,
    parse_range,
    range_finding,
)
from guardrails.context.config import adapter_declarations
from guardrails.context.config.reader import config_section, structure_settings
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches,
    matches_any,
    repo_paths,
)

RULE = "TEST.tier-selectors"


@dataclass(frozen=True)
class Selection:
    paths: tuple[str, ...]
    reason: str


def t2_files(paths: list[str], config: dict[str, Any]) -> list[str]:
    locations = adapter_declarations.tier_locations(config).get("t2", [])
    journeys = as_list(
        config_section(config_section(config, "tests"), "verification_surface").get(
            "journey_file_pattern"
        )
    )
    return sorted(
        path
        for path in paths
        if (matches_any(path, locations) and is_test_file(path, config))
        or any(fnmatchcase(path, pattern) for pattern in journeys)
    )


def area_of_test(path: str, config: dict[str, Any]) -> str | None:
    for pattern, area in config_section(
        config_section(structure_settings(config), "ownership_areas"), "map"
    ).items():
        if matches(path, str(pattern)) and isinstance(area, str):
            return area
    return None


def owner_of(path: str, config: dict[str, Any]) -> str | None:
    owners = config_section(structure_settings(config), "owners")
    files = config_section(owners, "files")
    exact = files.get(path)
    if isinstance(exact, str):
        return exact
    directories = config_section(owners, "directories")
    best = None
    for prefix, owner in directories.items():
        if (
            isinstance(owner, str)
            and matches(path, str(prefix))
            and (best is None or len(str(prefix)) > len(best[0]))
        ):
            best = (str(prefix), owner)
    return best[1] if best else None


def is_production(path: str, config: dict[str, Any]) -> bool:
    layout = config_section(config_section(config, "tests"), "layout")
    holders = [
        value
        for value in (layout.get("fixtures_dir"), layout.get("support_dir"))
        if value
    ]
    scope = as_list(
        config_section(config_section(config, "tests"), "coverage").get("scope")
    )
    return (
        matches_any(path, scope)
        and not is_test_file(path, config)
        and not matches_any(path, [str(holder) for holder in holders])
    )


def select(
    changed: list[str], inventory: list[str], config: dict[str, Any]
) -> Selection:
    candidates = t2_files(inventory, config)
    production = [path for path in changed if is_production(path, config)]
    unowned = [path for path in production if owner_of(path, config) is None]
    if unowned:
        return Selection(
            tuple(candidates),
            f"all T2: changed production code has no owner in structure.owners: {unowned}",
        )
    areas = {owner_of(path, config) for path in production}
    changed_t2 = set(t2_files(changed, config)) & set(candidates)
    picked = [
        path
        for path in candidates
        if area_of_test(path, config) in areas or path in changed_t2
    ]
    named = ", ".join(sorted(area for area in areas if area)) or "none"
    return Selection(
        tuple(picked), f"areas {named}; changed T2 files {len(changed_t2)}"
    )


def affected(repo: Path, config: dict[str, Any], argv: list[str]) -> Selection:
    diff = parse_range(argv)
    changed = [path for change in changed_files(repo, diff) for path in change.paths]
    try:
        inventory = repo_paths(repo)
    except InventoryError as error:
        raise DiffError(str(error)) from error
    return select(changed, inventory, config)


@dataclass(frozen=True)
class TierCommand:
    name: str
    suffixes: tuple[str, ...]
    arguments: Callable[[object, list[str]], list[str]]


def print_affected(
    repo: Path, config: dict[str, Any], command: TierCommand, base: str, head: str
) -> list[Finding]:
    try:
        selection = affected(repo, config, ["--base", base, "--head", head])
    except DiffError as error:
        return [range_finding(RULE, error)]
    print(f"selection: {selection.reason}", file=sys.stderr)
    paths = adapter_declarations.claimed(
        config, command.name, "t2", selection.paths, command.suffixes
    )
    if paths:
        tiers = adapter_declarations.adapter_tiers(config, command.name)
        selector = tiers.get("t2", {}).get("selector", "")
        print(shlex.join(command.arguments(selector, paths)))
    return []
