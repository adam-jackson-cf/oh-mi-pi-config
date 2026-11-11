"""Read the shipped house-style data and give word, casing, version and test-name verdicts.

``omp_guardrails/data/house-style.json`` is the one source of style truth (plan §1.2). Repos
never copy or edit it; they record its ``version`` and any ADR-backed ``exceptions`` in their
profile's ``house_style`` section. Stacks without this package pin ``digest()``.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from importlib import resources
from pathlib import PurePosixPath

from omp_guardrails.findings import Finding

DATA_FILE = "house-style.json"


def data_bytes() -> bytes:
    return resources.files("omp_guardrails.data").joinpath(DATA_FILE).read_bytes()


@cache
def load_house_style() -> dict:
    return json.loads(data_bytes())


def digest() -> str:
    """SHA-256 of the shipped data, for stacks that pin a hashed copy."""
    return hashlib.sha256(data_bytes()).hexdigest()


def stem(name: str) -> str:
    """A file stem is the part before its first dot; ``.hidden`` names keep their dot."""
    if name.startswith("."):
        return name
    return name.split(".", 1)[0]


def is_banned_word(name: str) -> bool:
    return name in load_house_style()["banned_words"]


def matches_casing(name: str, casings: list[str]) -> bool:
    patterns = load_house_style()["casing_patterns"]
    return any(re.fullmatch(patterns[casing], name) for casing in casings)


def ecosystem(name: str) -> dict:
    return load_house_style()["casing"][name]


def version_problem(segment: str, *, last: bool, standalone_allowed: bool) -> str | None:
    """Why a path segment's version token breaks the rule, or None when it does not."""
    rules = load_house_style()["versioning"]
    name = stem(segment) if last else segment
    if re.fullmatch(rules["standalone"], name):
        return None if standalone_allowed else f"version segment '{segment}'"
    if re.search(rules["token"], name) or re.search(rules["attached_suffix"], name):
        return f"version token in name '{segment}'"
    return None


def child_repeats_parent(parts: tuple[str, ...]) -> str | None:
    """The first segment that repeats its parent folder's name (a file compares by stem)."""
    if load_house_style()["child_repeats_parent"] != "banned":
        return None
    for index in range(1, len(parts)):
        child = stem(parts[index]) if index == len(parts) - 1 else parts[index]
        if child == parts[index - 1]:
            return parts[index]
    return None


def is_catalogue_plural(name: str) -> bool:
    return name in load_house_style()["catalogue_plurals"]


def test_name_problems(file_name: str) -> list[str]:
    """Ticket, version, date and generic-only problems in a test file's name."""
    rules = load_house_style()["test_roots"]["name_tokens"]
    tokens = [token for token in re.split(r"[-_.]+", stem(file_name).lower()) if token]
    behaviour = [token for token in tokens if token not in rules["ignored"]]
    problems = []
    for token in behaviour:
        if re.fullmatch(rules["date"], token):
            problems.append(f"date token '{token}'")
        elif re.fullmatch(rules["ticket"], token):
            problems.append(f"ticket token '{token}'")
        elif re.fullmatch(rules["version"], token):
            problems.append(f"version token '{token}'")
        elif (
            re.fullmatch(rules["trailing_number"], token)
            and token not in rules["numbered_allowlist"]
        ):
            problems.append(f"numbered token '{token}'")
    if not behaviour or all(is_banned_word(token) for token in behaviour):
        problems.append("no behaviour or owner name")
    return problems


def apply_exceptions(findings: list[Finding], profile: dict) -> list[Finding]:
    """Drop findings covered by the profile's ADR-backed ``house_style.exceptions``."""
    section = profile.get("house_style")
    exceptions = section.get("exceptions", []) if isinstance(section, dict) else []
    covered = [
        (item["rule"], item["path"].rstrip("/"))
        for item in exceptions
        if isinstance(item, dict) and item.get("adr") and item.get("rule") and item.get("path")
    ]
    return [
        finding
        for finding in findings
        if not any(
            finding.rule_id == rule and is_within(finding.path, path) for rule, path in covered
        )
    ]


def is_within(path: str, scope: str) -> bool:
    return path == scope or PurePosixPath(path).is_relative_to(scope)
