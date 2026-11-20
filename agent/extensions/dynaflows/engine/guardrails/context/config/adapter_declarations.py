from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from guardrails.foundation.inventory import matches_any

PACKAGE = "guardrails.commands.adapters"
TIER_KEYS = ("t1", "t2", "t3")


@dataclass(frozen=True)
class Declared:
    name: str
    tiers: dict[str, dict[str, Any]]


def as_texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def declared(profile: dict[str, Any]) -> list[Declared]:
    entries = profile.get("adapters")
    found = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            continue
        tiers = entry.get("tiers")
        found.append(
            Declared(
                entry["name"],
                {
                    key: spec
                    for key, spec in (tiers.items() if isinstance(tiers, dict) else [])
                    if key in TIER_KEYS and isinstance(spec, dict)
                },
            )
        )
    return found


def names(profile: dict[str, Any]) -> list[str]:
    return [entry.name for entry in declared(profile)]


def adapter_tiers(profile: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    for entry in declared(profile):
        if entry.name == name:
            return entry.tiers
    return {}


def extend_unique(bucket: list[str], items: list[str]) -> None:
    bucket.extend(item for item in items if item not in bucket)


def tier_locations(
    profile: dict[str, Any], name: str | None = None
) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for entry in declared(profile):
        if name is not None and entry.name != name:
            continue
        for tier, spec in entry.tiers.items():
            extend_unique(found.setdefault(tier, []), as_texts(spec.get("location")))
    return {tier: found[tier] for tier in TIER_KEYS if tier in found}


def locations(profile: dict[str, Any], name: str | None = None) -> list[str]:
    return list(
        dict.fromkeys(
            item for values in tier_locations(profile, name).values() for item in values
        )
    )


def claimed(
    profile: dict[str, Any],
    name: str,
    tier: str,
    paths: list[str] | tuple[str, ...],
    suffixes: tuple[str, ...],
) -> list[str]:
    own = tier_locations(profile, name).get(tier, [])
    every = tier_locations(profile).get(tier, [])
    return [
        path
        for path in paths
        if path.endswith(suffixes)
        and (matches_any(path, own) or not matches_any(path, every))
    ]


def module_name(name: str) -> str:
    return f"{PACKAGE}.{name}"


def adapter_part(name: str, part: str) -> ModuleType:
    return importlib.import_module(f"{module_name(name)}.{part}")


def installed(name: str) -> bool:
    if not name.isidentifier() or not name.islower():
        return False
    return importlib.util.find_spec(module_name(name)) is not None
