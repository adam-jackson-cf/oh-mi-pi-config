from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from guardrails.context.changes import (
    DiffError,
    changed_files,
    hunks,
    parse_range,
    range_finding,
)
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.reader import checker_settings
from guardrails.context.config.suite_files import is_test_source
from guardrails.foundation.findings import Finding

RULE = "TEST.no-evasion"
CHECKER = "timeout_increases"
DEFAULT_TIMEOUT_PATTERN = r"(?P<key>\b[A-Za-z_]*timeout[A-Za-z_0-9]*)[\"']?\s*[:=(]\s*(?P<value>\d+(?:\.\d+)?)"
FIX = (
    "keep the timeout and fix the slow or hanging behaviour, or move the work to the tier that "
    "owns it; never raise a timeout to get green"
)


def timeouts(
    lines: tuple[tuple[int, str], ...], pattern: re.Pattern[str]
) -> list[tuple[int, str, float]]:
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


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = checker_settings(config, CHECKER)
    pattern = re.compile(
        str(options.get("timeout_pattern") or DEFAULT_TIMEOUT_PATTERN), re.I
    )
    found: list[Finding] = []
    try:
        diff = parse_range(argv)
        for change in changed_files(repo, diff):
            if change.status in {"A", "D"} or not is_test_source(change.path, config):
                continue
            for part in hunks(repo, diff, change.path):
                found.extend(increases(change.path, part.removed, part.added, pattern))
    except DiffError as error:
        return [range_finding(RULE, error)]
    return apply_exceptions(found, config)
