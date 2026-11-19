"""TEST.no-evasion (diff part): a changed test never raises a timeout to get green.

Over ``git diff <base> <head>`` the checker pairs each hunk's removed and added lines of every
changed test file. Within one hunk, the n-th removed value of a timeout key and the n-th added
value of the same key form a pair; an added value larger than its pair is a finding. A new
timeout with no removed counterpart is not an increase (``TEST.subprocess-timeouts``, run by
``python-test-hygiene``, asks for them), and a removed timeout is not an increase either.

Test files are files under a tier ``location``, files matching ``layout.test_file_patterns``
(the house-style defaults when the profile has none) and ``conftest.py`` files.

Profile ``guardrails.no_evasion`` settings (optional):

- ``timeout_pattern``: one regular expression with ``key`` and ``value`` groups replacing the
  default, which matches ``timeout=5``, ``timeout(5)``, ``timeout: 5000`` and keys such as
  ``testTimeout`` or ``read_timeout_s``.

A finding is waived only by a reason-backed ``guardrails.exceptions`` entry for this rule.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.context.changes import (
    DiffError,
    changed_files,
    hunks,
    parse_range,
    range_finding,
)
from guardrails.context.config import adapter_declarations
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import matches_any, settings

RULE = "TEST.no-evasion"
CHECKER = "no_evasion"
DEFAULT_TIMEOUT_PATTERN = r"(?P<key>\b[A-Za-z_]*timeout[A-Za-z_0-9]*)[\"']?\s*[:=(]\s*(?P<value>\d+(?:\.\d+)?)"
FIX = (
    "keep the timeout and fix the slow or hanging behaviour, or move the work to the tier that "
    "owns it; never raise a timeout to get green"
)


def tier_locations(profile: dict[str, Any]) -> list[str]:
    """Every adapter's test locations across the tiers."""
    return adapter_declarations.locations(profile)


def is_changed_test(path: str, profile: dict[str, Any]) -> bool:
    if PurePosixPath(path).name == "conftest.py":
        return True
    return is_test_file(path, profile) or matches_any(path, tier_locations(profile))


def timeouts(
    lines: tuple[tuple[int, str], ...], pattern: re.Pattern[str]
) -> list[tuple[int, str, float]]:
    """``(line, key, value)`` for every timeout literal in the lines, in order."""
    found: list[tuple[int, str, float]] = []
    for number, text in lines:
        for match in pattern.finditer(text):
            found.append((number, match.group("key"), float(match.group("value"))))
    return found


def increases(
    path: str,
    removed: tuple[tuple[int, str], ...],
    added: tuple[tuple[int, str], ...],
    pattern: re.Pattern[str],
) -> list[Finding]:
    before: dict[str, list[float]] = {}
    for _, key, value in timeouts(removed, pattern):
        before.setdefault(key, []).append(value)
    seen: dict[str, int] = {}
    found = []
    for number, key, value in timeouts(added, pattern):
        index = seen.get(key, 0)
        seen[key] = index + 1
        olds = before.get(key, [])
        if index < len(olds) and value > olds[index]:
            found.append(
                Finding(
                    RULE,
                    path,
                    f"line {number} raises timeout '{key}' from {olds[index]:g} to {value:g}",
                    FIX,
                )
            )
    return found


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    """``guardrails diff-check --base <ref> [--head <ref>]``."""
    config = settings(profile, CHECKER)
    pattern = re.compile(
        str(config.get("timeout_pattern") or DEFAULT_TIMEOUT_PATTERN), re.I
    )
    found: list[Finding] = []
    try:
        diff = parse_range(argv)
        for change in changed_files(repo, diff):
            if change.status in {"A", "D"} or not is_changed_test(change.path, profile):
                continue
            for part in hunks(repo, diff, change.path):
                found.extend(increases(change.path, part.removed, part.added, pattern))
    except DiffError as error:
        return [range_finding(RULE, error)]
    return apply_exceptions(found, profile)
