"""Section schemas the repo config's tests, structure, houseStyle and checkers reuse.

repo_config.py is the validator; this module holds the shared leaf and section checks and
the exemption reading the checkers use.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeGuard

from guardrails.context.config.adapter_declarations import TIER_KEYS, installed
from guardrails.context.config.house_style import load_house_style
from guardrails.foundation.inventory import approved_targets, config_section

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
    """A config value a checker needs (e.g. tests.adapters) cannot drive the run."""


OPTIONAL_MARK = "?"
MACHINE = {
    "id": "text",
    "platform": "text",
    "runtime": "text",
    "wall_limit_s": "positive",
    "per_test_limit_s": "positive",
}
TIER = {"command": "text"}
ADAPTER_TIER = {"selector": "text", "location": "texts"}
ADAPTER = {
    "name": "text",
    "tiers": {"t1?": ADAPTER_TIER, "t2?": ADAPTER_TIER, "t3?": ADAPTER_TIER},
}
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


def exempt_tier_keys(tiers: object) -> frozenset[str]:
    """Tiers written as an exemption ({none: true}), whatever their approval."""
    return frozenset(
        tier
        for tier, value in (tiers.items() if isinstance(tiers, dict) else ())
        if f"tiers.{tier}" in EXEMPTABLE and is_exemption(value)
    )


def approved_exemption(config: dict[str, Any], target: str) -> bool:
    """tests.<target> is {none: true} and the owner approved the exemption."""
    parent, _, leaf = target.rpartition(".")
    tests = config_section(config, "tests")
    holder = tests.get(parent) if parent else tests
    return (
        target in EXEMPTABLE
        and isinstance(holder, dict)
        and is_exemption(holder.get(leaf))
        and target in approved_targets(config, "exemption")
    )


def exempt_tiers(config: dict[str, Any]) -> frozenset[str]:
    """Tiers the config exempts with an owner exemption approval."""
    tiers = config_section(config, "tests").get("tiers")
    return frozenset(
        tier
        for tier in (tiers if isinstance(tiers, dict) else {})
        if approved_exemption(config, f"tiers.{tier}")
    )


def adapter_problems(tests: dict[str, Any]) -> list[tuple[str, str]]:
    """Adapter declarations against the tiers of one tests section."""
    entries = tests.get("adapters")
    if not isinstance(entries, list):
        return []
    exempt = exempt_tier_keys(tests.get("tiers"))
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
                    f"takes part in tier {tier}, which tests.tiers exempts",
                )
                for tier in sorted(exempt.intersection(tiers))
            )
    problems.extend(
        ("adapters", f"no adapter takes part in tier {tier}")
        for tier in TIER_KEYS
        if tier not in covered and tier not in exempt
    )
    return problems


ExemptionCheck = Callable[[dict[str, Any], str, list[tuple[str, str]]], None]


@dataclass(frozen=True)
class SectionRules:
    exemption: ExemptionCheck
    unknown_key_problem: str


def child_key(key: str, name: str) -> str:
    return f"{key}.{name}" if key else name


def check_value(
    value: object,
    spec: Any,
    key: str,
    problems: list[tuple[str, str]],
    rules: SectionRules,
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
    rules: SectionRules,
) -> None:
    if not isinstance(value, dict):
        problems.append((key, "is missing" if value is None else "must be an object"))
        return
    names = {child.removesuffix(OPTIONAL_MARK): child for child in spec}
    for extra in sorted(set(value) - set(names)):
        problems.append((child_key(key, extra), rules.unknown_key_problem))
    for name, child in names.items():
        if child.endswith(OPTIONAL_MARK) and name not in value:
            continue
        check_value(value.get(name), spec[child], child_key(key, name), problems, rules)


def check_records(
    value: object,
    spec: tuple[Any, ...],
    key: str,
    problems: list[tuple[str, str]],
    rules: SectionRules,
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
