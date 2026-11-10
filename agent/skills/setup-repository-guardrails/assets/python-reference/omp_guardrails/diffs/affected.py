"""Affected T2 selection from the profile's Ownership areas (test-guidance v2, tiers).

``omp-guardrails affected --base <ref> [--head <ref>]`` prints the T2 test files the change
affects, one per line, and a ``selection:`` line on standard error that says why.

1. T2 files are the repo's test files under the ``tiers.t2.location`` paths, plus files matching
   ``verification_surface.journey_file_pattern`` (journeys are T2). Test files are files that
   match ``layout.test_file_patterns``.
2. Each T2 file's area is the area of the first ``ownership_areas.map`` pattern it matches.
3. A changed file is production code when it lies under ``coverage.scope`` and is not a test,
   fixture or support file. Its area is the owner ``structure.owners`` gives it: an exact
   ``files`` entry first, then the longest matching ``directories`` prefix.
4. The selection is every T2 file in an affected area, plus every changed T2 file.
5. When any changed production file has no owner, the selection is all T2 files and the
   ``selection:`` line names the unowned files. Unknown ownership never narrows the run.

Changed files that are neither production code nor T2 files (docs, other tiers) select nothing.
The command reports no findings; a bad diff range is a ``TEST.tiers`` finding.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from omp_guardrails.diffs.changes import DiffError, changed_files, parse_range, range_finding
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import (
    InventoryError,
    as_list,
    matches,
    matches_any,
    repo_paths,
)
from omp_guardrails.structure.naming import is_test_file

RULE = "TEST.tiers"


@dataclass(frozen=True)
class Selection:
    paths: tuple[str, ...]
    reason: str


def section(profile: dict, key: str) -> dict:
    value = profile.get(key)
    return value if isinstance(value, dict) else {}


def t2_files(paths: list[str], profile: dict) -> list[str]:
    locations = as_list(section(section(profile, "tiers"), "t2").get("location"))
    journeys = as_list(section(profile, "verification_surface").get("journey_file_pattern"))
    return sorted(
        path
        for path in paths
        if (matches_any(path, locations) and is_test_file(path, profile))
        or any(fnmatchcase(path, pattern) for pattern in journeys)
    )


def area_of_test(path: str, profile: dict) -> str | None:
    for pattern, area in section(section(profile, "ownership_areas"), "map").items():
        if matches(path, str(pattern)) and isinstance(area, str):
            return area
    return None


def owner_of(path: str, profile: dict) -> str | None:
    owners = section(section(profile, "structure"), "owners")
    files = section(owners, "files")
    if isinstance(files.get(path), str):
        return files[path]
    directories = section(owners, "directories")
    best = None
    for prefix, owner in directories.items():
        if isinstance(owner, str) and matches(path, str(prefix)):
            if best is None or len(str(prefix)) > len(best[0]):
                best = (str(prefix), owner)
    return best[1] if best else None


def is_production(path: str, profile: dict) -> bool:
    layout = section(profile, "layout")
    holders = [value for value in (layout.get("fixtures_dir"), layout.get("support_dir")) if value]
    scope = as_list(section(profile, "coverage").get("scope"))
    return (
        matches_any(path, scope)
        and not is_test_file(path, profile)
        and not matches_any(path, [str(holder) for holder in holders])
    )


def select(changed: list[str], inventory: list[str], profile: dict) -> Selection:
    candidates = t2_files(inventory, profile)
    production = [path for path in changed if is_production(path, profile)]
    unowned = [path for path in production if owner_of(path, profile) is None]
    if unowned:
        return Selection(
            tuple(candidates),
            f"all T2: changed production code has no owner in structure.owners: {unowned}",
        )
    areas = {owner_of(path, profile) for path in production}
    changed_t2 = set(t2_files(changed, profile)) & set(candidates)
    picked = [
        path for path in candidates if area_of_test(path, profile) in areas or path in changed_t2
    ]
    named = ", ".join(sorted(area for area in areas if area)) or "none"
    return Selection(tuple(picked), f"areas {named}; changed T2 files {len(changed_t2)}")


def affected(repo: Path, profile: dict, argv: list[str]) -> Selection:
    diff = parse_range(argv)
    changed = [path for change in changed_files(repo, diff) for path in change.paths]
    try:
        inventory = repo_paths(repo)
    except InventoryError as error:
        raise DiffError(str(error)) from error
    return select(changed, inventory, profile)


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    try:
        selection = affected(repo, profile, argv)
    except DiffError as error:
        return [range_finding(RULE, error)]
    print(f"selection: {selection.reason}", file=sys.stderr)
    for path in selection.paths:
        print(path)
    return []
