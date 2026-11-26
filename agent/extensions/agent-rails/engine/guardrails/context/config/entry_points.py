"""tests.public_entry_points: the packages a repository owns and each one's public entry point.

Every adapter and python_policy read the section here. Each key is an owned package as its
language names it (a Python package, a folder, a package.json name) and each value is
"<module>:<symbol>"; section_schema.ENTRY_POINT is the form. Each language resolves the
module itself: a dotted Python module, a TypeScript file path or package name, a Go
import path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from guardrails.context.config.reader import tests_settings
from guardrails.context.config.section_schema import (
    ENTRY_POINT,
    ConfigValueError,
    entry_points_problem,
)

SECTION = "public_entry_points"


@dataclass(frozen=True)
class EntryPoint:
    package: str
    module: str
    symbol: str


def entry_points(config: dict[str, Any]) -> tuple[EntryPoint, ...]:
    """The configured entry points, none when the section is absent or empty; a
    ConfigValueError when it is not the documented form."""
    value = tests_settings(config, SECTION)
    if value is None or value == {}:
        return ()
    problem = entry_points_problem(value)
    if problem is not None:
        raise ConfigValueError(f"tests.{SECTION} {problem}")
    found = []
    for package, entry in value.items():
        match = ENTRY_POINT.fullmatch(entry)
        if match is not None:
            found.append(EntryPoint(package, match["module"], match["symbol"]))
    return tuple(found)


def packages(config: dict[str, Any]) -> tuple[str, ...]:
    """The owned packages, in config order."""
    return tuple(point.package for point in entry_points(config))
