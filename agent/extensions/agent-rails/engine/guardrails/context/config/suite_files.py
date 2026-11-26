from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from guardrails.context.config import adapter_declarations
from guardrails.context.config.reader import tests_settings
from guardrails.foundation import conventions
from guardrails.foundation.inventory import as_list, matches_any, name_matches
from guardrails.foundation.source.tree import language_of

# One adapter's test files: its tier locations and the configured test file patterns.
Suite = tuple[list[str], list[str]]


def test_patterns(config: dict[str, Any]) -> list[str]:
    layout = tests_settings(config, "layout")
    patterns = (
        as_list(layout.get("test_file_patterns")) if isinstance(layout, dict) else []
    )
    return patterns


def is_test_file(path: str, config: dict[str, Any]) -> bool:
    return any(name_matches(path, pattern) for pattern in test_patterns(config))


def test_holders(config: dict[str, Any]) -> list[str]:
    """The layout's fixtures and support folders: everything in them is test source."""
    layout = tests_settings(config, "layout")
    return (
        as_list([layout.get("fixtures_dir"), layout.get("support_dir")])
        if isinstance(layout, dict)
        else []
    )


def tests_data(path: str, config: dict[str, Any]) -> bool:
    """A file under an adapter's tier location that is no Python, TypeScript or Go
    source: the tier's data, goldens and snapshots. A source file there is a test only by
    its name, because a runner that finds tests beside code (vitest, go test) has the
    code's own folder as its location."""
    return language_of(path) is None and matches_any(
        path, adapter_declarations.locations(config)
    )


def is_test_source(path: str, config: dict[str, Any]) -> bool:
    """Test source: a file a test runner reads as support by name (conftest.py), a test
    file by its language pack's runner discovery patterns or the configured test file
    patterns, a file in a folder its pack names as a test holder (tests/, fixtures/,
    support/), a file under test_holders, or tests_data."""
    if conventions.test_support_file(path):
        return True
    patterns = [*conventions.test_patterns(path), *test_patterns(config)]
    if any(name_matches(path, pattern) for pattern in patterns):
        return True
    placement = conventions.placement(path)
    folders = {placement[key] for key in ("root", "fixtures_dir", "support_dir")}
    return (
        bool(folders.intersection(PurePosixPath(path).parts[:-1]))
        or matches_any(path, test_holders(config))
        or tests_data(path, config)
    )


def adapter_suite(config: dict[str, Any], name: str) -> Suite:
    return adapter_declarations.locations(config, name), test_patterns(config)


def in_suite(path: str, suite: Suite) -> bool:
    locations, patterns = suite
    return matches_any(path, locations) or any(
        name_matches(path, pattern) for pattern in patterns
    )
