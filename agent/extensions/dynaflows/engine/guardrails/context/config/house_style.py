from __future__ import annotations

import hashlib
import json
import re
from functools import cache
from importlib import resources
from typing import Any

from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import matches

DATA_FILE = "house-style.json"


def data_bytes() -> bytes:
    return resources.files("guardrails.data").joinpath(DATA_FILE).read_bytes()


@cache
def load_house_style() -> dict[str, Any]:
    style: dict[str, Any] = json.loads(data_bytes())
    return style


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


def version_problem(
    segment: str, *, last: bool, standalone_allowed: bool
) -> str | None:
    """Why a path segment's version token breaks the rule, or None when it does not."""
    rules = load_house_style()["versioning"]
    name = stem(segment) if last else segment
    if re.fullmatch(rules["standalone"], name):
        return None if standalone_allowed else f"version segment '{segment}'"
    if re.search(rules["token"], name) or re.search(rules["attached_suffix"], name):
        return f"version token in name '{segment}'"
    return None


def _name_tokens(name: str) -> tuple[str, ...]:
    """Split separators, lower-to-upper transitions and acronym-to-word transitions."""
    name = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return tuple(token.lower() for token in re.split(r"[_\-.]+", name) if token)


def child_repeats_parent(
    parts: tuple[str, ...], *, existing_paths: set[str] | None = None
) -> str | None:
    if load_house_style()["child_repeats_parent"] != "banned":
        return None
    for index in range(1, len(parts)):
        last = index == len(parts) - 1
        child = stem(parts[index]) if last else parts[index]
        segment_path = "/".join(parts[: index + 1])
        if existing_paths is not None and any(
            item == segment_path or item.startswith(segment_path + "/")
            for item in existing_paths
        ):
            continue
        if (last and conventions.fixed_file("/".join(parts))) or (
            not last and conventions.fixed_folder("/".join(parts), parts[index])
        ):
            continue
        parent_tokens = _name_tokens(parts[index - 1])
        child_tokens = _name_tokens(child)
        width = len(parent_tokens)
        if width and any(
            child_tokens[start : start + width] == parent_tokens
            for start in range(len(child_tokens) - width + 1)
        ):
            return parts[index]
    return None


def is_catalogue_plural(name: str) -> bool:
    return name in load_house_style()["catalogue_plurals"]


def test_name_problems(file_name: str) -> list[str]:
    """Ticket, version, date and generic-only problems in a test file's name."""
    rules = load_house_style()["test_names"]
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


def apply_exceptions(findings: list[Finding], profile: dict[str, Any]) -> list[Finding]:
    section = profile.get("guardrails")
    exceptions = section.get("exceptions", []) if isinstance(section, dict) else []
    covered = [
        item
        for item in exceptions
        if isinstance(item, dict)
        and item.get("reason")
        and item.get("rule")
        and item.get("path")
    ]
    return [
        finding
        for finding in findings
        if not any(
            finding.rule_id == item["rule"]
            and matches(finding.path, item["path"])
            and ("check" not in item or item["check"] == finding.check)
            for item in covered
        )
    ]
