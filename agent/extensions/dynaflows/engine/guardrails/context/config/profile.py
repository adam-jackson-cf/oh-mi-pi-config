from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeGuard

from guardrails.context.config.adapter_declarations import TIER_KEYS, installed
from guardrails.context.config.house_style import load_house_style
from guardrails.foundation.findings import Finding

PROFILE_FILE = "profile.md"
PROFILE_PATH = "record:profile.md"
RULE = "PROFILE.complete"
FENCE = "---"
EXEMPTABLE = frozenset({
    "tiers.t3",
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
    pass


def load_profile(record: Path) -> dict[str, Any]:
    path = record / PROFILE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ProfileError(f"{PROFILE_PATH} does not exist") from error
    except (OSError, UnicodeError) as error:
        raise ProfileError(f"{PROFILE_PATH} cannot be read: {error}") from error
    return parse_front_matter(text)


def parse_front_matter(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        raise ProfileError(f"{PROFILE_PATH} must start with a '---' front-matter block")
    try:
        end = next(
            index for index, line in enumerate(lines[1:], 1) if line.strip() == FENCE
        )
    except StopIteration as error:
        raise ProfileError(
            f"{PROFILE_PATH} front matter has no closing '---' line"
        ) from error
    try:
        values = json.loads("\n".join(lines[1:end]))
    except json.JSONDecodeError as error:
        raise ProfileError(
            f"{PROFILE_PATH} front matter is not valid JSON: {error}"
        ) from error
    if not isinstance(values, dict):
        raise ProfileError(f"{PROFILE_PATH} front matter must be one JSON object")
    return values


OPTIONAL_MARK = "?"
MACHINE = {
    "id": "text",
    "platform": "text",
    "runtime": "text",
    "wall_limit_s": "positive",
    "per_test_limit_s": "positive",
    "approval": "text",
}
TIER = {"command": "text"}
ADAPTER_TIER = {"selector": "text", "location": "texts"}
ADAPTER = {
    "name": "text",
    "tiers": {"t1?": ADAPTER_TIER, "t2?": ADAPTER_TIER, "t3?": ADAPTER_TIER},
}
EXCEPTION = {"rule": "text", "path": "text", "reason": "text", "check?": "text"}
SCHEMA: dict[str, Any] = {
    "tiers": {"t1": TIER, "t2": TIER, "t3": TIER},
    "public_entry_points": "map",
    "run_events": {
        "commit": "text",
        "ci": "text",
        "nightly_scheduler": "text",
        "nightly_branches": "texts",
    },
    "fast_tier_budget": {
        "duration_report_command": "text",
        "machines": ("records", MACHINE),
    },
    "coverage": {
        "scope": "texts",
        "line_floor": "percent",
        "branch_floor": "percent",
        "where": "text",
    },
    "guards": {"process": "text", "network": "text", "user_data": "text"},
    "environments": {
        "required": "texts",
        "require_flag": "text",
        "readiness_probe": "text",
    },
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
    "layout": {
        "support_dir": "text",
        "fixtures_dir": "text",
        "test_file_patterns": "texts",
    },
    "command_notes": {
        "working_directory": "text",
        "interpretation": "text",
        "lsp?": "value",
    },
    "adapters": ("records", ADAPTER),
    "structure": {"owners": "value", "tiers": "list", "import_direction": "value"},
    "house_style": {"version": "integer"},
}
GUARDRAILS_SECTIONS = (
    "adapter_bun_test",
    "adapter_go",
    "adapter_pytest",
    "adapter_vitest",
    "check",
    "coverage_ratchet",
    "feature_map",
    "golden_record",
    "guarded_run",
    "identity_artefacts",
    "journeys",
    "lever_selftest",
    "naming",
    "no_evasion",
    "paths",
    "python",
    "structure",
    "test_evidence",
    "toolchain_parity",
)
OPTIONAL = {
    "guardrails": {
        **{f"{section}{OPTIONAL_MARK}": "object" for section in GUARDRAILS_SECTIONS},
        "exceptions?": ("optional_records", EXCEPTION),
    }
}


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


def validate_profile(profile: dict[str, Any]) -> list[Finding]:
    problems: list[tuple[str, str]] = []
    for key in sorted(set(profile) - set(SCHEMA) - set(OPTIONAL)):
        problems.append((key, "is not a profile key; remove it or fix its spelling"))
    for key, spec in SCHEMA.items():
        check_value(profile.get(key), spec, key, problems)
    for key, spec in OPTIONAL.items():
        if key in profile:
            check_value(profile[key], spec, key, problems)
    problems.extend(house_style_version_problems(profile))
    problems.extend(adapter_problems(profile))
    return [
        Finding(
            RULE, PROFILE_PATH, f"{key} {problem}", "record the owner-approved value"
        )
        for key, problem in problems
    ]


def house_style_version_problems(profile: dict[str, Any]) -> list[tuple[str, str]]:
    declared = profile.get("house_style", {})
    version = declared.get("version") if isinstance(declared, dict) else None
    problem = house_style_version_problem(version)
    return [] if problem is None else [("house_style.version", problem)]


def house_style_version_problem(version: object) -> str | None:
    if not isinstance(version, int) or isinstance(version, bool):
        return None
    shipped = load_house_style()["version"]
    if version == shipped:
        return None
    return (
        f"is {version} but the installed house style is version {shipped}; "
        "review the house-style changes and record the new version"
    )


def is_exemption(value: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict) and "none" in value


def exempt_tiers(profile: dict[str, Any]) -> frozenset[str]:
    tiers = profile.get("tiers")
    return frozenset(
        tier
        for tier, value in (tiers.items() if isinstance(tiers, dict) else ())
        if f"tiers.{tier}" in EXEMPTABLE and is_exemption(value)
    )


def adapter_problems(profile: dict[str, Any]) -> list[tuple[str, str]]:
    entries = profile.get("adapters")
    if not isinstance(entries, list):
        return []
    exempt = exempt_tiers(profile)
    problems: list[tuple[str, str]] = []
    seen: set[str] = set()
    covered: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        key = f"adapters[{index}]"
        name = entry.get("name")
        if isinstance(name, str) and not is_missing_text(name):
            if name in seen:
                problems.append((f"{key}.name", f"repeats adapter {name!r}"))
            elif not installed(name):
                problems.append((
                    f"{key}.name",
                    f"{name!r} is not an installed adapter package",
                ))
            seen.add(name)
        tiers = entry.get("tiers")
        if isinstance(tiers, dict):
            if not tiers:
                problems.append((f"{key}.tiers", "needs at least one tier"))
            covered.update(tier for tier in tiers if tier in TIER_KEYS)
            problems.extend(
                (
                    f"{key}.tiers.{tier}",
                    f"takes part in tier {tier}, which the profile exempts",
                )
                for tier in sorted(exempt.intersection(tiers))
            )
    problems.extend(
        ("adapters", f"no adapter takes part in tier {tier}")
        for tier in TIER_KEYS
        if tier not in covered and tier not in exempt
    )
    return problems


def check_exemption(
    value: dict[str, Any], key: str, problems: list[tuple[str, str]]
) -> None:
    extra = sorted(set(value) - {"none", "approval"})
    if extra:
        problems.append((key, f"exemption has unexpected keys {extra}"))
    if is_missing_text(value.get("none")):
        problems.append((key, "exemption needs a real 'none' reason"))
    if is_missing_text(value.get("approval")):
        problems.append((key, "exemption needs an owner 'approval' reference"))


def profile_unknown_key(key: str, extra: str) -> str:
    if key == "house_style" and extra == "exceptions":
        return (
            "is obsolete; move entries to guardrails.exceptions and replace the old "
            "justification field with reason"
        )
    return "is not a profile key; remove it or fix its spelling"


ExemptionCheck = Callable[[dict[str, Any], str, list[tuple[str, str]]], None]
UnknownKey = Callable[[str, str], str]


@dataclass(frozen=True)
class SectionRules:
    exemption: ExemptionCheck
    unknown_key: UnknownKey


PROFILE_RULES = SectionRules(check_exemption, profile_unknown_key)


def child_key(key: str, name: str) -> str:
    return f"{key}.{name}" if key else name


def check_value(
    value: object,
    spec: Any,
    key: str,
    problems: list[tuple[str, str]],
    rules: SectionRules = PROFILE_RULES,
) -> None:
    if key in EXEMPTABLE and is_exemption(value):
        rules.exemption(value, key, problems)
        return
    if isinstance(spec, dict):
        check_section(value, spec, key, problems, rules)
    elif isinstance(spec, tuple):
        check_records(value, spec, key, problems, rules)
    else:
        check_leaf(value, spec, key, problems)


def check_section(
    value: object,
    spec: dict[str, Any],
    key: str,
    problems: list[tuple[str, str]],
    rules: SectionRules = PROFILE_RULES,
) -> None:
    if not isinstance(value, dict):
        problems.append((key, "is missing" if value is None else "must be an object"))
        return
    names = {child.removesuffix(OPTIONAL_MARK): child for child in spec}
    for extra in sorted(set(value) - set(names)):
        problems.append((child_key(key, extra), rules.unknown_key(key, extra)))
    for name, child in names.items():
        if child.endswith(OPTIONAL_MARK) and name not in value:
            continue
        check_value(value.get(name), spec[child], child_key(key, name), problems, rules)


def check_records(
    value: object,
    spec: tuple[Any, ...],
    key: str,
    problems: list[tuple[str, str]],
    rules: SectionRules = PROFILE_RULES,
) -> None:
    kind, fields = spec
    if not isinstance(value, list):
        problems.append((key, "must be a list"))
        return
    if not value and kind == "records":
        problems.append((key, "needs at least one entry"))
    for index, item in enumerate(value):
        check_section(item, fields, f"{key}[{index}]", problems, rules)


def check_leaf(
    value: object, kind: str, key: str, problems: list[tuple[str, str]]
) -> None:
    problem = leaf_problem(value, kind)
    if problem:
        problems.append((key, problem))


def text_problem(value: object) -> str | None:
    return (
        "is missing (placeholder, sentinel or empty)"
        if is_missing_text(value)
        else None
    )


def texts_problem(value: object) -> str | None:
    items = value if isinstance(value, list) else [value]
    if not items or any(is_missing_text(item) for item in items):
        return "needs one or more real values"
    return None


def map_problem(value: object) -> str | None:
    if not isinstance(value, dict) or not value:
        return "must be a non-empty object"
    return placeholder_problem(value)


def object_problem(value: object) -> str | None:
    return None if isinstance(value, dict) else "must be an object"


def list_problem(value: object) -> str | None:
    return (
        "must be a list" if not isinstance(value, list) else placeholder_problem(value)
    )


def value_problem(value: object) -> str | None:
    if value in ({}, [], ""):
        return "is missing"
    return placeholder_problem(value)


LEAF_CHECKS: dict[str, Callable[[object], str | None]] = {
    "text": text_problem,
    "texts": texts_problem,
    "map": map_problem,
    "object": object_problem,
    "list": list_problem,
}
NUMBER_KINDS = frozenset({"percent", "positive", "integer"})


def leaf_problem(value: object, kind: str) -> str | None:
    if value is None:
        return "is missing"
    if kind in NUMBER_KINDS:
        return number_problem(value, kind)
    return LEAF_CHECKS.get(kind, value_problem)(value)


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


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    del repo, argv
    return validate_profile(profile)
