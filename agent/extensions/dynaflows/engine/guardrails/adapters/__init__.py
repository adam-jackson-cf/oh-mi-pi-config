"""Test-framework adapters (plan §1.12), one subpackage per framework.

An adapter belongs to a test framework, not to a tier. The profile's ``adapters`` list names
every framework the repository uses; each entry gives, per tier it takes part in, the native
``selector`` and the ``location`` paths handed to that framework::

    "adapters": [
      {"name": "pytest", "tiers": {"t1": {"selector": "fast", "location": "tests"}}},
      {"name": "bun_test", "tiers": {"t1": {"selector": "--test-name-pattern fast",
                                             "location": "tests"}}}
    ]

Every tier may hold tests from every framework; a tier's locations are the union of its
adapters' locations. ``name`` is the subpackage; ``guardrails adapter <name> <action>`` runs
its ``run(repo, profile, argv)``. An adapter only produces evidence for the shared checkers and
supplies its framework's source patterns: it emits JUnit XML, lists test IDs per tier, guards
the fast tier, ships source rules, names its mutation tool and normalises its JUnit dialect.

This module reads the declaration for every consumer; ``guardrails profile-check`` validates it
(``guardrails.config.profile``), so the readers here take what is well-formed and skip the rest.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any

PACKAGE = "guardrails.adapters"
TIER_KEYS = ("t1", "t2", "t3")


@dataclass(frozen=True)
class Declared:
    """One profile ``adapters`` entry: the framework and its per-tier selection."""

    name: str
    tiers: dict[str, dict[str, Any]]


def as_texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def declared(profile: dict[str, Any]) -> list[Declared]:
    """The well-formed ``adapters`` entries, in profile order."""
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
    """One adapter's tier specs (``selector``, ``location``), keyed by tier."""
    for entry in declared(profile):
        if entry.name == name:
            return entry.tiers
    return {}


def tier_locations(
    profile: dict[str, Any], name: str | None = None
) -> dict[str, list[str]]:
    """Tier -> the locations of every adapter (or only ``name``) that takes part in it."""
    found: dict[str, list[str]] = {}
    for entry in declared(profile):
        if name is not None and entry.name != name:
            continue
        for tier, spec in entry.tiers.items():
            bucket = found.setdefault(tier, [])
            bucket.extend(
                item for item in as_texts(spec.get("location")) if item not in bucket
            )
    return {tier: found[tier] for tier in TIER_KEYS if tier in found}


def locations(profile: dict[str, Any], name: str | None = None) -> list[str]:
    """Every test location of the declared adapters (or only ``name``), without repeats."""
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
    """The paths adapter ``name`` runs in ``tier``: files of a type it runs that lie under its
    own locations for the tier, or under no adapter's location for it (journeys, for example)."""
    from guardrails.structure.inventory import matches_any

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


def installed(name: str) -> bool:
    """True when ``name`` is an adapter subpackage of this engine."""
    if not name.isidentifier() or not name.islower():
        return False
    return importlib.util.find_spec(module_name(name)) is not None
