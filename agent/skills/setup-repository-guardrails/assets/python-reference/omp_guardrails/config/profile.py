"""Load the repo test profile's machine-readable values and validate PROFILE.complete.

Format choice: ``.omp/test-profile.md`` starts with a front-matter block between two ``---``
lines that holds one JSON object. JSON is valid YAML flow syntax, so Markdown and YAML tools
treat the block as ordinary front matter, while this package reads it with the standard
library's ``json`` module and needs no YAML parser. The human sections follow the block.

PROFILE.complete: every required value is present and real. Template placeholders
(``<...>``), ``n/a``, ``TBD``, empty values and a bare ``none`` count as missing. Only
``verification_surface``, ``run_events.nightly_scheduler`` and ``identity_artefacts`` may be
``{"none": "<reason>", "approval": "<reference>"}``, and only with both fields filled. A
``null`` anywhere inside a free-form value counts as missing too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from omp_guardrails.findings import Finding

PROFILE_PATH = ".omp/test-profile.md"
RULE = "PROFILE.complete"
FENCE = "---"
EXEMPTABLE = frozenset({
    "verification_surface",
    "run_events.nightly_scheduler",
    "identity_artefacts",
})
SENTINELS = frozenset({
    "",
    "n/a",
    "na",
    "tbd",
    "tba",
    "todo",
    "none",
    "null",
    "unknown",
    "-",
    "?",
    "...",
})
PLACEHOLDER = re.compile(r"<[^<>\n]+>|\bTBD\b|\bTODO\b", re.IGNORECASE)


class ProfileError(Exception):
    """The profile file is absent or its front matter cannot be read."""


def load_profile(repo: Path) -> dict:
    """Return the JSON object in the front matter of ``<repo>/.omp/test-profile.md``."""
    path = repo / PROFILE_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ProfileError(f"{PROFILE_PATH} does not exist") from error
    except (OSError, UnicodeError) as error:
        raise ProfileError(f"{PROFILE_PATH} cannot be read: {error}") from error
    return parse_front_matter(text)


def parse_front_matter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        raise ProfileError(f"{PROFILE_PATH} must start with a '---' front-matter block")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == FENCE)
    except StopIteration as error:
        raise ProfileError(f"{PROFILE_PATH} front matter has no closing '---' line") from error
    try:
        values = json.loads("\n".join(lines[1:end]))
    except json.JSONDecodeError as error:
        raise ProfileError(f"{PROFILE_PATH} front matter is not valid JSON: {error}") from error
    if not isinstance(values, dict):
        raise ProfileError(f"{PROFILE_PATH} front matter must be one JSON object")
    return values


# Leaf kinds: text (one real string), texts (a string or a non-empty list of strings),
# percent (0..100), positive (> 0), integer, map (non-empty object), object (any object),
# value (any non-empty value), list (a list, possibly empty). A tuple ("records", spec) is a
# non-empty list of objects matching spec; ("optional_records", spec) may be empty. A section key
# ending in OPTIONAL_MARK may be left out; when present its value is checked like any other.
OPTIONAL_MARK = "?"
MACHINE = {
    "id": "text",
    "platform": "text",
    "runtime": "text",
    "wall_limit_s": "positive",
    "per_test_limit_s": "positive",
    "approval": "text",
}
TIER = {"selector": "text", "location": "texts", "command": "text"}
EXCEPTION = {"rule": "text", "path": "text", "adr": "text"}
SCHEMA: dict[str, Any] = {
    "tiers": {"t1": TIER, "t2": TIER, "t3": TIER},
    "public_entry_points": "map",
    "run_events": {
        "commit": "text",
        "ci": "text",
        "nightly_scheduler": "text",
        "nightly_branches": "texts",
    },
    "fast_tier_budget": {"duration_report_command": "text", "machines": ("records", MACHINE)},
    "coverage": {
        "scope": "texts",
        "line_floor": "percent",
        "branch_floor": "percent",
        "where": "text",
    },
    "guards": {"process": "text", "network": "text", "user_data": "text"},
    "environments": {"required": "texts", "require_flag": "text", "readiness_probe": "text"},
    "verification_surface": {
        "lever_command": "text",
        "doctor_marker": "text",
        "feature_map_path": "text",
        "journey_file_pattern": "text",
        "data_dir_arg": "text",
        "evidence_dir_arg": "text",
    },
    "ownership_areas": {"map": "map", "affected_t2_selection": "text"},
    "identity_artefacts": "value",
    "layout": {"support_dir": "text", "fixtures_dir": "text", "test_file_patterns": "texts"},
    # ``lsp`` is written by ``omp-guardrails init``; the other two are the owner's notes.
    "command_notes": {"working_directory": "text", "interpretation": "text", "lsp?": "value"},
    "adapter": "text",
    "structure": {"owners": "value", "tiers": "list", "import_direction": "value"},
    "house_style": {"version": "integer", "exceptions": ("optional_records", EXCEPTION)},
}
# Checker settings may hold regular expressions, so only their shape is checked here.
OPTIONAL = {"guardrails": "object"}


def is_missing_text(value: object) -> bool:
    if not isinstance(value, str):
        return True
    text = value.strip()
    lowered = text.lower()
    return (
        lowered in SENTINELS
        or lowered.startswith(("none:", "none "))
        or bool(PLACEHOLDER.search(text))
    )


def validate_profile(profile: dict) -> list[Finding]:
    """PROFILE.complete findings for a parsed profile (empty when complete)."""
    problems: list[tuple[str, str]] = []
    for key in sorted(set(profile) - set(SCHEMA) - set(OPTIONAL)):
        problems.append((key, "is not a profile key; remove it or fix its spelling"))
    for key, spec in SCHEMA.items():
        check_value(profile.get(key), spec, key, problems)
    for key, spec in OPTIONAL.items():
        if key in profile:
            check_value(profile[key], spec, key, problems)
    problems.extend(house_style_version_problems(profile))
    return [
        Finding(RULE, PROFILE_PATH, f"{key} {problem}", "record the owner-approved value")
        for key, problem in problems
    ]


def house_style_version_problems(profile: dict) -> list[tuple[str, str]]:
    from omp_guardrails.config.house_style import load_house_style

    declared = profile.get("house_style", {})
    version = declared.get("version") if isinstance(declared, dict) else None
    if not isinstance(version, int) or isinstance(version, bool):
        return []
    shipped = load_house_style()["version"]
    if version == shipped:
        return []
    return [
        (
            "house_style.version",
            f"is {version} but the installed house style is version {shipped}; "
            "review the house-style changes and record the new version",
        )
    ]


def check_value(value: object, spec: Any, key: str, problems: list[tuple[str, str]]) -> None:
    if key in EXEMPTABLE and isinstance(value, dict) and "none" in value:
        check_exemption(value, key, problems)
        return
    if isinstance(spec, dict):
        check_section(value, spec, key, problems)
    elif isinstance(spec, tuple):
        check_records(value, spec, key, problems)
    else:
        check_leaf(value, spec, key, problems)


def check_exemption(value: dict, key: str, problems: list[tuple[str, str]]) -> None:
    extra = sorted(set(value) - {"none", "approval"})
    if extra:
        problems.append((key, f"exemption has unexpected keys {extra}"))
    if is_missing_text(value.get("none")):
        problems.append((key, "exemption needs a real 'none' reason"))
    if is_missing_text(value.get("approval")):
        problems.append((key, "exemption needs an owner 'approval' reference"))


def check_section(value: object, spec: dict, key: str, problems: list[tuple[str, str]]) -> None:
    if not isinstance(value, dict):
        problems.append((key, "is missing" if value is None else "must be an object"))
        return
    names = {child.removesuffix(OPTIONAL_MARK): child for child in spec}
    for extra in sorted(set(value) - set(names)):
        problems.append((f"{key}.{extra}", "is not a profile key; remove it or fix its spelling"))
    for name, child in names.items():
        if child.endswith(OPTIONAL_MARK) and name not in value:
            continue
        check_value(value.get(name), spec[child], f"{key}.{name}", problems)


def check_records(value: object, spec: tuple, key: str, problems: list[tuple[str, str]]) -> None:
    kind, fields = spec
    if not isinstance(value, list):
        problems.append((key, "must be a list"))
        return
    if not value and kind == "records":
        problems.append((key, "needs at least one entry"))
    for index, item in enumerate(value):
        check_section(item, fields, f"{key}[{index}]", problems)


def check_leaf(value: object, kind: str, key: str, problems: list[tuple[str, str]]) -> None:
    problem = leaf_problem(value, kind)
    if problem:
        problems.append((key, problem))


def leaf_problem(value: object, kind: str) -> str | None:
    if value is None:
        return "is missing"
    if kind == "text":
        return "is missing (placeholder, sentinel or empty)" if is_missing_text(value) else None
    if kind == "texts":
        items = value if isinstance(value, list) else [value]
        if not items or any(is_missing_text(item) for item in items):
            return "needs one or more real values"
        return None
    if kind in {"percent", "positive", "integer"}:
        return number_problem(value, kind)
    if kind == "map":
        if not isinstance(value, dict) or not value:
            return "must be a non-empty object"
        return placeholder_problem(value)
    if kind == "object":
        return None if isinstance(value, dict) else "must be an object"
    if kind == "list":
        return "must be a list" if not isinstance(value, list) else placeholder_problem(value)
    if value in ({}, [], ""):
        return "is missing"
    return placeholder_problem(value)


def number_problem(value: object, kind: str) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "must be a number"
    if kind == "integer" and not isinstance(value, int):
        return "must be an integer"
    if kind == "percent" and not 0 <= value <= 100:
        return "must be between 0 and 100"
    if kind == "positive" and value <= 0:
        return "must be greater than 0"
    return None


def placeholder_problem(value: object) -> str | None:
    """Nested free-form values may not carry nulls, placeholders or bare sentinels anywhere."""
    if value is None:
        return "holds a null value"
    if isinstance(value, dict):
        for item in [*value.keys(), *value.values()]:
            problem = placeholder_problem(item)
            if problem:
                return problem
        return None
    if isinstance(value, list):
        return next((p for item in value if (p := placeholder_problem(item))), None)
    if isinstance(value, str) and is_missing_text(value):
        return f"holds a placeholder or sentinel value {value!r}"
    return None


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails profile-check``: the CLI has already loaded the front matter."""
    del repo, argv
    return validate_profile(profile)
