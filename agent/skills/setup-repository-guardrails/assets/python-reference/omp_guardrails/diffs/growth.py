"""TEST.growth.justified and TEST.duplicate-path over ``git diff <base> <head>``.

``omp-guardrails diff-check --base <ref> [--head <ref>]`` collects the test files at both
commits through the profile adapter's static case collector (``omp_guardrails.adapters.
<adapter>.cases``, for pytest ``omp_guardrails.adapters.pytest.cases``). Test files are files
matching ``layout.test_file_patterns`` (the house-style defaults when the profile has none).

- TEST.growth.justified: every selector added at head (a new test file, function or
  parameter row) needs an entry in the behaviour map at head, naming a non-test source file
  changed in the range and the behaviour it adds or alters::

      {"schemaVersion": 1,
       "tests": {"tests/test_cart.py::test_total[empty]":
                 {"source": "src/cart.py", "behavior": "an empty cart totals zero"}}}

  An absent or malformed map, a missing entry, an unchanged source or a placeholder behaviour
  is a finding naming the test, fixed by mapping or removing it. A changed test file the
  adapter cannot collect statically is a finding too.
- TEST.duplicate-path: a case added at head whose fingerprint equals an existing case or an
  earlier added case is a finding naming both, fixed by merging or removing one.

Profile ``guardrails.test_growth`` settings (optional):

- ``map_path`` (default ``.omp/test-behavior-map.json``): the behaviour map.

A finding is waived only by an ADR-backed ``house_style.exceptions`` entry for its rule.
"""

from __future__ import annotations

import importlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from omp_guardrails.config.house_style import apply_exceptions
from omp_guardrails.config.profile import is_missing_text
from omp_guardrails.diffs.changes import (
    DiffError,
    DiffRange,
    changed_files,
    file_at,
    git,
    parse_range,
    range_finding,
)
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import settings
from omp_guardrails.structure.naming import is_test_file

GROWTH = "TEST.growth.justified"
DUPLICATE = "TEST.duplicate-path"
CHECKER = "test_growth"
DEFAULT_MAP_PATH = ".omp/test-behavior-map.json"
MAP_SCHEMA_VERSION = 1
ADAPTER_NAME = re.compile(r"[a-z][a-z0-9_]*")
MAP_FIX = "map or remove the added test"
DUPLICATE_FIX = "merge or remove one of the duplicate tests"


@dataclass(frozen=True)
class Snapshot:
    """The statically collected tests of one commit."""

    fingerprints: dict[str, str]
    selectors: set[str]
    errors: dict[str, str]


Collector = Callable[[str, str], object]


def adapter_collector(profile: dict) -> Collector | None:
    """The adapter's ``cases.collect``, or None when the adapter ships no static collector."""
    name = profile.get("adapter")
    if not isinstance(name, str) or not ADAPTER_NAME.fullmatch(name):
        return None
    try:
        module = importlib.import_module(f"omp_guardrails.adapters.{name}.cases")
    except ModuleNotFoundError:
        return None
    return getattr(module, "collect", None)


def test_blobs(repo: Path, ref: str, profile: dict) -> dict[str, str]:
    """``{path: blob id}`` of the test files at ``ref``."""
    output = git(repo, "ls-tree", "-r", "-z", ref)
    blobs = {}
    for entry in output.split("\0"):
        if not entry or "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        kind, blob = meta.split()[1:3]
        if kind == "blob" and is_test_file(path, profile):
            blobs[path] = blob
    return blobs


def snapshot(
    repo: Path, blobs: Mapping[str, str], collect: Collector, cache: dict[str, object]
) -> Snapshot:
    """Collect each file once per blob, so files unchanged between commits parse once."""
    fingerprints: dict[str, str] = {}
    selectors: set[str] = set()
    errors: dict[str, str] = {}
    for path, blob in sorted(blobs.items()):
        key = f"{path}\0{blob}"
        if key not in cache:
            try:
                cache[key] = collect(path, git(repo, "cat-file", "blob", blob))
            except ValueError as error:
                cache[key] = error
        result = cache[key]
        if isinstance(result, ValueError):
            errors[path] = str(result)
            selectors.add(path)
            continue
        selectors.update(result.selectors)
        fingerprints.update((case.selector, case.fingerprint) for case in result.cases)
    return Snapshot(fingerprints, selectors, errors)


def valid_map(document: object) -> dict[str, dict] | None:
    if not isinstance(document, dict) or document.get("schemaVersion") != MAP_SCHEMA_VERSION:
        return None
    tests = document.get("tests")
    if not isinstance(tests, dict):
        return None
    if not all(isinstance(entry, dict) for entry in tests.values()):
        return None
    return tests


def load_map(repo: Path, ref: str, map_path: str) -> tuple[bool, dict[str, dict] | None]:
    """Whether the map exists at ``ref``, and its entries when it is well formed."""
    text = file_at(repo, ref, map_path)
    if text is None:
        return False, None
    try:
        return True, valid_map(json.loads(text))
    except ValueError:
        return True, None


def entry_problem(entry: dict | None, changed_sources: frozenset[str]) -> str | None:
    if entry is None:
        return "has no behaviour map entry"
    source, behaviour = entry.get("source"), entry.get("behavior")
    if not isinstance(source, str) or source not in changed_sources:
        return f"maps to {source!r}, which is not a source file changed in this range"
    if is_missing_text(behaviour):
        return "maps to no real behaviour (missing, placeholder or sentinel)"
    return None


def growth_findings(
    added: set[str],
    changed_sources: frozenset[str],
    map_state: tuple[bool, dict[str, dict] | None],
    map_path: str,
) -> list[Finding]:
    if not added:
        return []
    exists, tests = map_state
    if not exists:
        return [
            Finding(GROWTH, map_path, f"{len(added)} added test selectors need this map", MAP_FIX)
        ]
    if tests is None:
        return [
            Finding(
                GROWTH,
                map_path,
                f"is not a schemaVersion {MAP_SCHEMA_VERSION} map with a 'tests' object",
                f"repair the map, then {MAP_FIX}",
            )
        ]
    found = []
    for selector in sorted(added):
        problem = entry_problem(tests.get(selector), changed_sources)
        if problem:
            path = selector.split("::", 1)[0]
            found.append(Finding(GROWTH, path, f"{selector} {problem} in {map_path}", MAP_FIX))
    return found


def duplicate_findings(base: Mapping[str, str], head: Mapping[str, str]) -> list[Finding]:
    """New cases whose fingerprint matches an existing case or an earlier new case."""
    seen: dict[str, str] = {}
    for selector, fingerprint in sorted(base.items()):
        seen.setdefault(fingerprint, selector)
    for selector in sorted(head.keys() & base.keys()):
        seen.setdefault(head[selector], selector)
    found = []
    for selector in sorted(head.keys() - base.keys()):
        fingerprint = head[selector]
        if fingerprint in seen:
            path = selector.split("::", 1)[0]
            message = f"{selector} duplicates the call path and assertions of {seen[fingerprint]}"
            found.append(Finding(DUPLICATE, path, message, DUPLICATE_FIX))
        else:
            seen[fingerprint] = selector
    return found


def collection_findings(head: Snapshot, changed: set[str]) -> list[Finding]:
    return [
        Finding(GROWTH, path, f"cannot collect tests statically: {error}", MAP_FIX)
        for path, error in sorted(head.errors.items())
        if path in changed
    ]


def check(repo: Path, profile: dict, diff: DiffRange) -> list[Finding]:
    map_path = str(settings(profile, CHECKER).get("map_path") or DEFAULT_MAP_PATH)
    changes = changed_files(repo, diff)
    changed_paths = {path for change in changes for path in change.paths}
    if not any(is_test_file(path, profile) for path in changed_paths):
        return []
    collect = adapter_collector(profile)
    if collect is None:
        return [
            Finding(
                GROWTH,
                ".omp/test-profile.md",
                f"adapter {profile.get('adapter')!r} has no static test-case collector",
                "name an adapter that ships one, or add 'cases.collect' to the adapter",
            )
        ]
    changed_sources = frozenset(
        path for path in changed_paths if path != map_path and not is_test_file(path, profile)
    )
    cache: dict[str, object] = {}
    base = snapshot(repo, test_blobs(repo, diff.base, profile), collect, cache)
    head = snapshot(repo, test_blobs(repo, diff.head, profile), collect, cache)
    found = collection_findings(head, changed_paths)
    found.extend(
        growth_findings(
            head.selectors - base.selectors,
            changed_sources,
            load_map(repo, diff.head, map_path),
            map_path,
        )
    )
    found.extend(duplicate_findings(base.fingerprints, head.fingerprints))
    return found


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails diff-check --base <ref> [--head <ref>]``."""
    try:
        found = check(repo, profile, parse_range(argv))
    except DiffError as error:
        return [range_finding(GROWTH, error)]
    return apply_exceptions(found, profile)
