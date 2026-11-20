from __future__ import annotations

from collections import Counter
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from guardrails.context.config.profile import exempt_tiers
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches_any,
    name_matches,
    repo_paths,
)
from guardrails.toolkit.test_report.junit import Report, UsageError, evidence_settings

PROPAGATION = "TEST.failure-propagation"
EVASION = "TEST.no-evasion"
SELECTORS = "TEST.tier-selectors"
SKIP_FIX = (
    "make the test run and pass, or record an approved_skips entry with the owner's "
    "reason and approval reference"
)
RETRY_FIX = "fix the flake's cause so it passes on the first run; never rely on retries"
DUPLICATE_FIX = "give every test a unique ID so each result maps to one test"
EMPTY_FIX = "check the tier selector and command; an empty collection never passes"


def approved(test_id: str, profile: dict[str, Any]) -> bool:
    entries = evidence_settings(profile).get("approved_skips")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        pattern, reason, approval = (
            entry.get(key) for key in ("id", "reason", "approval")
        )
        if not all(
            isinstance(value, str) and value.strip()
            for value in (pattern, reason, approval)
        ):
            continue
        if isinstance(pattern, str) and fnmatchcase(test_id, pattern):
            return True
    return False


def report_findings(report: Report, profile: dict[str, Any]) -> list[Finding]:
    if not report.cases:
        return [
            Finding(
                PROPAGATION, report.source, "run report holds no test cases", EMPTY_FIX
            )
        ]
    found = []
    by_id: dict[str, list[Any]] = {}
    for case in report.cases:
        by_id.setdefault(case.id, []).append(case)
    for test_id, cases in by_id.items():
        path = cases[-1].file or report.source
        final = cases[-1]
        earlier_failed = any(case.outcome in {"failed", "error"} for case in cases[:-1])
        if final.outcome == "passed" and (final.retries > 0 or earlier_failed):
            attempts = final.retries + len(cases) - 1
            found.append(
                Finding(
                    EVASION,
                    path,
                    f"{test_id} passed only after {attempts} retries",
                    RETRY_FIX,
                )
            )
        elif len(cases) > 1:
            found.append(
                Finding(
                    PROPAGATION,
                    path,
                    f"{test_id} is reported {len(cases)} times",
                    DUPLICATE_FIX,
                )
            )
        if final.outcome in {"skipped", "xfail"} and not approved(test_id, profile):
            reason = f" ({final.reason})" if final.reason else ""
            found.append(
                Finding(
                    EVASION,
                    path,
                    f"{test_id} is {final.outcome} without approval{reason}",
                    SKIP_FIX,
                )
            )
    return found


def tier_arguments(argv: list[str]) -> dict[str, list[str]]:
    lists: dict[str, list[str]] = {}
    index = 0
    while index < len(argv):
        flag, _, inline = argv[index].partition("=")
        if flag == "--tier":
            value = inline or (argv[index + 1] if index + 1 < len(argv) else "")
            index += 0 if inline else 1
            tier, sep, path = value.partition("=")
            if not sep or not tier or not path:
                raise UsageError("--tier takes <tier>=<collect-only list path>")
            if path in lists.get(tier, []):
                raise UsageError(f"--tier {tier}={path} given twice")
            lists.setdefault(tier, []).append(path)
        index += 1
    return lists


def read_ids(path: Path) -> list[str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def joined_ids(repo: Path, paths: list[str]) -> list[str] | None:
    joined: list[str] = []
    for path in paths:
        ids = read_ids(repo / path)
        if ids is None:
            return None
        joined.extend(ids)
    return joined


def id_file(test_id: str) -> str:
    return test_id.split("::", 1)[0]


def profile_tiers(profile: dict[str, Any]) -> list[str]:
    tiers = profile.get("tiers")
    if not isinstance(tiers, dict):
        return []
    return sorted(set(tiers) - exempt_tiers(profile))


def collection_findings(
    collections: dict[str, list[str] | None],
    profile: dict[str, Any],
    test_files: list[str],
) -> list[Finding]:
    tiers = profile_tiers(profile)
    found = [
        Finding(
            SELECTORS,
            tier,
            f"tier {tier} has no collect-only list",
            f"pass --tier {tier}=<list> from the adapter's tier listing",
        )
        for tier in tiers
        if tier not in collections
    ]
    file_tiers: dict[str, set[str]] = {}
    for tier, ids in sorted(collections.items()):
        found.extend(tier_list_findings(tier, ids, tiers))
        if tier in tiers and ids is not None:
            for test_id in ids:
                file_tiers.setdefault(id_file(test_id), set()).add(tier)
    for file, selecting in sorted(file_tiers.items()):
        if len(selecting) > 1:
            found.append(
                Finding(
                    SELECTORS,
                    file,
                    f"selected by tiers {', '.join(sorted(selecting))}",
                    "make the tier selectors disjoint: one tier per test file",
                )
            )
    if all(ids is not None for ids in collections.values()):
        found.extend(
            Finding(
                SELECTORS,
                file,
                "test file is selected by no tier",
                "move it under a tier location or fix the tier selectors",
            )
            for file in test_files
            if file not in file_tiers
        )
    return found


def tier_list_findings(
    tier: str, ids: list[str] | None, tiers: list[str]
) -> list[Finding]:
    if tier not in tiers:
        return [
            Finding(
                SELECTORS,
                tier,
                f"tier {tier} is not a profile tier",
                "use a profile tier key",
            )
        ]
    if ids is None:
        return [
            Finding(
                PROPAGATION,
                tier,
                f"tier {tier} collect-only list is missing",
                EMPTY_FIX,
            )
        ]
    found = []
    if not ids:
        found.append(
            Finding(PROPAGATION, tier, f"tier {tier} collected no tests", EMPTY_FIX)
        )
    found.extend(
        Finding(
            SELECTORS,
            id_file(test_id),
            f"{test_id} is collected {count} times in tier {tier}",
            DUPLICATE_FIX,
        )
        for test_id, count in sorted(Counter(ids).items())
        if count > 1
    )
    return found


def repository_test_files(repo: Path, profile: dict[str, Any]) -> list[str]:
    layout = profile.get("layout")
    patterns = (
        as_list(layout.get("test_file_patterns")) if isinstance(layout, dict) else []
    )
    exempt = as_list(evidence_settings(profile).get("unselected_exempt"))
    return [
        path
        for path in repo_paths(repo)
        if any(name_matches(path, pattern) for pattern in patterns)
        and not matches_any(path, exempt)
        and (repo / path).is_file()
    ]


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        lists = tier_arguments(argv)
    except UsageError as error:
        return [
            Finding(
                SELECTORS, "collection-check", f"usage: {error}", "fix the arguments"
            )
        ]
    collections = {tier: joined_ids(repo, paths) for tier, paths in lists.items()}
    try:
        test_files = repository_test_files(repo, profile)
    except InventoryError as error:
        return [
            Finding(
                SELECTORS,
                "collection-check",
                f"cannot list test files: {error}",
                "run inside a git work tree",
            )
        ]
    return collection_findings(collections, profile, test_files)
