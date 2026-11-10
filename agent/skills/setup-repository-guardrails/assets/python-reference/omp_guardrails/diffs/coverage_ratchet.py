"""TEST.coverage-ratchet: coverage only ratchets up unless the owner approves the change.

Over ``git diff <base> <head>`` the checker reports:

1. an added coverage pragma (``no cover``, ``no branch``, ``istanbul ignore``, ``c8 ignore``,
   ``v8 ignore``) in any changed file;
2. a lowered or removed floor in a coverage config file (``fail_under``, ``--cov-fail-under``,
   ``lines``, ``branches``, ``statements``, ``functions``);
3. an added line inside a coverage exclusion setting (``omit``, ``exclude_lines``,
   ``exclude_also``, ``coveragePathIgnorePatterns``, a vitest/c8 ``exclude`` list ...);
4. a lowered ``coverage.line_floor`` or ``coverage.branch_floor`` in the test profile.

A finding is cleared only by an approval the same diff adds to the head profile:
``guardrails.coverage_ratchet.approvals: [{"path": "<path or glob>", "approval": "<ref>"}]``.
Approvals already present at the base do not count, so one standing approval cannot disable
the ratchet.

Profile ``guardrails.coverage_ratchet`` settings (all optional):

- ``config_files``: patterns for coverage config files (default: common Python and JS ones).
- ``pragma_pattern``: one regular expression for pragmas (default above).
- ``pragma_skip``: patterns of files whose added lines are prose, not code (default docs).
- ``floor_keys`` and ``exclusion_keys``: key names replacing the defaults above.
- ``approvals``: see above.
"""

from __future__ import annotations

import re
from pathlib import Path

from omp_guardrails.config.profile import PROFILE_PATH, is_missing_text
from omp_guardrails.diffs.changes import (
    DiffError,
    DiffRange,
    changed_files,
    file_at,
    line_changes,
    parse_range,
    profile_at,
    range_finding,
)
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, matches, name_matches, settings

RULE = "TEST.coverage-ratchet"
CHECKER = "coverage_ratchet"
FIX = (
    "keep coverage at or above its floor with outcome or property cases, or add an owner "
    "approval for this path to guardrails.coverage_ratchet.approvals in this change"
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
OPENERS = "[({"
CLOSERS = "])}"
CLOSING = frozenset({"", "]", ")", "}"})


def key_pattern(keys: list[str], value: str) -> re.Pattern[str]:
    names = "|".join(re.escape(key) for key in sorted(keys, key=len, reverse=True))
    return re.compile(rf"(?:^|[\s,{{\"'-])(?P<key>{names})[\"']?\s*[:=]\s*{value}")


def bracket_delta(text: str) -> int:
    return sum(text.count(char) for char in OPENERS) - sum(text.count(char) for char in CLOSERS)


def indent(text: str) -> int:
    return len(text) - len(text.lstrip())


def exclusion_line_numbers(text: str, key_re: re.Pattern[str]) -> set[int]:
    """1-based line numbers that belong to an exclusion setting's value."""
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
            # INI-style continuation: indented lines under a key with an empty value.
            key_indent = indent(lines[index])
            while (
                follow < len(lines) and lines[follow].strip() and indent(lines[follow]) > key_indent
            ):
                numbers.add(follow + 1)
                follow += 1
        index = follow
    return numbers


def floors(lines: tuple[tuple[int, str], ...], floor_re: re.Pattern[str]) -> dict[str, float]:
    found: dict[str, float] = {}
    for _, text in lines:
        for match in floor_re.finditer(text):
            found[match.group("key")] = float(match.group("value"))
    return found


def config_findings(repo: Path, diff: DiffRange, path: str, config: dict) -> list[Finding]:
    changes = line_changes(repo, diff, path)
    floor_re = key_pattern(
        as_list(config.get("floor_keys")) or DEFAULT_FLOOR_KEYS, r"(?P<value>\d+(?:\.\d+)?)"
    )
    found: list[Finding] = []
    before, after = floors(changes.removed, floor_re), floors(changes.added, floor_re)
    for key, old in sorted(before.items()):
        new = after.get(key)
        if new is None:
            found.append(Finding(RULE, path, f"coverage floor '{key}' {old:g} was removed", FIX))
        elif new < old:
            found.append(
                Finding(RULE, path, f"coverage floor '{key}' lowered from {old:g} to {new:g}", FIX)
            )
    head_text = file_at(repo, diff.head, path)
    if head_text is None:
        return found
    exclusion_re = key_pattern(as_list(config.get("exclusion_keys")) or DEFAULT_EXCLUSION_KEYS, "")
    excluded = exclusion_line_numbers(head_text, exclusion_re)
    moved = {entry_text(text) for _, text in changes.removed}
    for number, text in changes.added:
        entry = entry_text(text)
        if number in excluded and entry not in CLOSING and entry not in moved:
            found.append(
                Finding(RULE, path, f"line {number} adds a coverage exclusion: {text.strip()}", FIX)
            )
    return found


def entry_text(line: str) -> str:
    """A list entry without layout: a re-indented or re-comma'd entry is not a new one."""
    return line.strip().rstrip(",").strip()


def pragma_findings(
    repo: Path, diff: DiffRange, path: str, pragma: re.Pattern[str]
) -> list[Finding]:
    """Added pragma lines; a pragma line that only moved within the file is not new."""
    changes = line_changes(repo, diff, path)
    moved = {text.strip() for _, text in changes.removed}
    return [
        Finding(RULE, path, f"line {number} adds a coverage pragma: {text.strip()}", FIX)
        for number, text in changes.added
        if pragma.search(text) and text.strip() not in moved
    ]


def profile_floor_findings(base: dict, head: dict) -> list[Finding]:
    found = []
    for key in ("line_floor", "branch_floor"):
        old = section_number(base, key)
        new = section_number(head, key)
        if old is not None and new is not None and new < old:
            found.append(
                Finding(RULE, PROFILE_PATH, f"coverage.{key} lowered from {old:g} to {new:g}", FIX)
            )
    return found


def section_number(profile: dict, key: str) -> float | None:
    coverage = profile.get("coverage")
    value = coverage.get(key) if isinstance(coverage, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def approvals(profile: dict) -> set[tuple[str, str]]:
    entries = settings(profile, CHECKER).get("approvals")
    found = set()
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        path, approval = entry.get("path"), entry.get("approval")
        if isinstance(path, str) and path and not is_missing_text(approval):
            found.add((path, str(approval)))
    return found


def is_config(path: str, patterns: list[str]) -> bool:
    return any(name_matches(path, pattern) for pattern in patterns)


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails diff-check --base <ref> [--head <ref>]``."""
    config = settings(profile, CHECKER)
    try:
        diff = parse_range(argv)
        changes = changed_files(repo, diff)
        base_profile, head_profile = profile_at(repo, diff.base), profile_at(repo, diff.head)
        pragma = re.compile(str(config.get("pragma_pattern") or DEFAULT_PRAGMA), re.IGNORECASE)
        config_files = as_list(config.get("config_files")) or DEFAULT_CONFIG_FILES
        skip = as_list(config.get("pragma_skip")) or DEFAULT_PRAGMA_SKIP
        found = profile_floor_findings(base_profile, head_profile)
        for change in changes:
            if change.status == "D":
                continue
            path = change.path
            if is_config(path, config_files):
                found.extend(config_findings(repo, diff, path, config))
            if not any(name_matches(path, pattern) for pattern in skip):
                found.extend(pragma_findings(repo, diff, path, pragma))
    except DiffError as error:
        return [range_finding(RULE, error)]
    new_approvals = approvals(head_profile) - approvals(base_profile)
    return [
        finding
        for finding in found
        if not any(matches(finding.path, pattern) for pattern, _ in new_approvals)
    ]
