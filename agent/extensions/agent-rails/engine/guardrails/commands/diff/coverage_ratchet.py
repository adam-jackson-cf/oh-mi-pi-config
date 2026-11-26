from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.context.changes import (
    Change,
    DiffError,
    DiffRange,
    LineChanges,
    changed_files,
    file_at,
    line_changes,
    parse_range,
    range_finding,
)
from guardrails.context.config.reader import approved_targets, checker_settings
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches, name_matches

RULE = "TEST.coverage-ratchet"
CHECKER = "coverage_ratchet"
FIX = (
    "keep coverage at or above its floor with outcome or property cases, or ask the owner "
    "for a ratchet approval of this path (approvals kind ratchet)"
)
DEFAULT_CONFIG_FILES = [
    ".coveragerc",
    "setup.cfg",
    "tox.ini",
    "pyproject.toml",
    "package.json",
    ".nycrc",
    ".nycrc.json",
    ".c8rc",
    ".c8rc.json",
    "vitest.config.*",
    "vite.config.*",
    "jest.config.*",
]
DEFAULT_PRAGMA = r"\bno\s+(?:cover|branch)\b|\b(?:istanbul|c8|v8)\s+ignore\b"
DEFAULT_PRAGMA_SKIP = ["*.md", "*.rst", "*.txt"]
DEFAULT_FLOOR_KEYS = [
    "fail_under",
    "fail-under",
    "cov-fail-under",
    "lines",
    "branches",
    "statements",
    "functions",
]
DEFAULT_EXCLUSION_KEYS = [
    "omit",
    "exclude_lines",
    "exclude_also",
    "partial_branches",
    "exclude",
    "coveragePathIgnorePatterns",
]
SECTIONED_FILES = ["setup.cfg", "tox.ini", "pyproject.toml"]
SECTION_HEADER = re.compile(r"^\s*\[\[?(?P<name>[\w.:\- ]+)\]\]?\s*(?:#.*)?$")
COVERAGE_SECTION = re.compile(
    r"^(?:tool\.coverage(?:\.|$)|coverage:|tool\.pytest(?:\.|$)|tool:pytest$|pytest$)"
)
OPENERS = "[({"
CLOSERS = "])}"
CLOSING = frozenset({"", "]", ")", "}"})


def key_pattern(keys: list[str], value: str) -> re.Pattern[str]:
    names = "|".join(re.escape(key) for key in sorted(keys, key=len, reverse=True))
    return re.compile(rf"(?:^|[\s,{{\"'-])(?P<key>{names})[\"']?\s*[:=]\s*{value}")


def bracket_delta(text: str) -> int:
    return sum(text.count(char) for char in OPENERS) - sum(
        text.count(char) for char in CLOSERS
    )


def indent(text: str) -> int:
    return len(text) - len(text.lstrip())


def exclusion_line_numbers(text: str, key_re: re.Pattern[str]) -> set[int]:
    lines = text.splitlines()
    numbers: set[int] = set()
    index = 0
    while index < len(lines):
        match = key_re.search(lines[index])
        if not match:
            index += 1
            continue
        numbers.add(index + 1)
        depth = bracket_delta(lines[index][match.end() :])
        follow = index + 1
        if depth > 0:
            while follow < len(lines) and depth > 0:
                numbers.add(follow + 1)
                depth += bracket_delta(lines[follow])
                follow += 1
        else:
            key_indent = indent(lines[index])
            while (
                follow < len(lines)
                and lines[follow].strip()
                and indent(lines[follow]) > key_indent
            ):
                numbers.add(follow + 1)
                follow += 1
        index = follow
    return numbers


@dataclass(frozen=True)
class ConfigChange:
    paths: tuple[str, ...]
    base_path: str | None
    head_path: str | None


def coverage_scope(path: str, text: str | None) -> set[int] | None:
    if text is None or not is_config(path, SECTIONED_FILES):
        return None
    numbers: set[int] = set()
    inside = False
    for number, line in enumerate(text.splitlines(), 1):
        header = SECTION_HEADER.match(line)
        if header:
            inside = bool(COVERAGE_SECTION.match(header.group("name").strip()))
        elif inside:
            numbers.add(number)
    return numbers


def scoped(
    lines: tuple[tuple[int, str], ...], scope: set[int] | None
) -> tuple[tuple[int, str], ...]:
    return tuple(line for line in lines if scope is None or line[0] in scope)


def floors(
    lines: tuple[tuple[int, str], ...], floor_re: re.Pattern[str]
) -> dict[str, float]:
    found: dict[str, float] = {}
    for _, text in lines:
        for match in floor_re.finditer(text):
            found[match.group("key")] = float(match.group("value"))
    return found


def coverage_changes(
    repo: Path, diff: DiffRange, target: ConfigChange
) -> tuple[LineChanges, str | None]:
    changes = line_changes(repo, diff, *target.paths)
    base, head = target.base_path, target.head_path
    base_text = None if base is None else file_at(repo, diff.base, base)
    head_text = None if head is None else file_at(repo, diff.head, head)
    removed = scoped(
        changes.removed, None if base is None else coverage_scope(base, base_text)
    )
    added = scoped(
        changes.added, None if head is None else coverage_scope(head, head_text)
    )
    return LineChanges(added, removed), head_text


def config_findings(
    repo: Path, diff: DiffRange, target: ConfigChange, options: dict[str, Any]
) -> list[Finding]:
    path = target.head_path or target.paths[0]
    changes, head_text = coverage_changes(repo, diff, target)
    removed, added = changes.removed, changes.added
    floor_re = key_pattern(
        as_list(options.get("floor_keys")) or DEFAULT_FLOOR_KEYS,
        r"(?P<value>\d+(?:\.\d+)?)",
    )
    found: list[Finding] = []
    before, after = floors(removed, floor_re), floors(added, floor_re)
    for key, old in sorted(before.items()):
        new = after.get(key)
        if new is None:
            found.append(
                Finding(RULE, path, f"coverage floor '{key}' {old:g} was removed", FIX)
            )
        elif new < old:
            found.append(
                Finding(
                    RULE,
                    path,
                    f"coverage floor '{key}' lowered from {old:g} to {new:g}",
                    FIX,
                )
            )
    if head_text is None:
        return found
    exclusion_re = key_pattern(
        as_list(options.get("exclusion_keys")) or DEFAULT_EXCLUSION_KEYS, ""
    )
    excluded = exclusion_line_numbers(head_text, exclusion_re)
    moved = {entry_text(text) for _, text in removed}
    for number, text in added:
        entry = entry_text(text)
        if number in excluded and entry not in CLOSING and entry not in moved:
            found.append(
                Finding(
                    RULE,
                    path,
                    f"line {number} adds a coverage exclusion: {text.strip()}",
                    FIX,
                )
            )
    return found


def change_config_findings(
    repo: Path,
    diff: DiffRange,
    change: Change,
    config_files: list[str],
    options: dict[str, Any],
) -> list[Finding]:
    paths = tuple(name for name in change.paths if is_config(name, config_files))
    if not paths:
        return []
    kept = change.status != "D" and is_config(change.path, config_files)
    base = change.paths[0]
    target = ConfigChange(
        paths,
        base if change.status != "A" and is_config(base, config_files) else None,
        change.path if kept else None,
    )
    return config_findings(repo, diff, target, options)


def entry_text(line: str) -> str:
    return line.strip().rstrip(",").strip()


def pragma_findings(
    repo: Path, diff: DiffRange, path: str, pragma: re.Pattern[str]
) -> list[Finding]:
    changes = line_changes(repo, diff, path)
    moved = {text.strip() for _, text in changes.removed}
    return [
        Finding(
            RULE, path, f"line {number} adds a coverage pragma: {text.strip()}", FIX
        )
        for number, text in changes.added
        if pragma.search(text) and text.strip() not in moved
    ]


def is_config(path: str, patterns: list[str]) -> bool:
    return any(name_matches(path, pattern) for pattern in patterns)


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = checker_settings(config, CHECKER)
    try:
        diff = parse_range(argv)
        changes = changed_files(repo, diff)
        pragma = re.compile(
            str(options.get("pragma_pattern") or DEFAULT_PRAGMA), re.IGNORECASE
        )
        config_files = as_list(options.get("config_files")) or DEFAULT_CONFIG_FILES
        skip = as_list(options.get("pragma_skip")) or DEFAULT_PRAGMA_SKIP
        found: list[Finding] = []
        for change in changes:
            path = change.path
            found.extend(
                change_config_findings(repo, diff, change, config_files, options)
            )
            if change.status == "D":
                continue
            if not any(name_matches(path, pattern) for pattern in skip):
                found.extend(pragma_findings(repo, diff, path, pragma))
    except DiffError as error:
        return [range_finding(RULE, error)]
    approved = approved_targets(config, "ratchet")
    return [
        finding
        for finding in found
        if not any(matches(finding.path, pattern) for pattern in approved)
    ]
