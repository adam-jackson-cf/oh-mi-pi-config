from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from guardrails import adapters
from guardrails.config.house_style import apply_exceptions
from guardrails.config.profile import PROFILE_PATH, is_missing_text
from guardrails.diffs.changes import (
    DiffError,
    DiffRange,
    changed_files,
    git,
    parse_range,
    range_finding,
)
from guardrails.findings import Finding
from guardrails.structure.inventory import matches_any
from guardrails.structure.naming import is_test_file

GROWTH = "TEST.growth.justified"
DUPLICATE = "TEST.duplicate-path"
BEHAVIOUR_FIX = "name the test after the behavior it proves, or remove the test"
DUPLICATE_FIX = "merge or remove one of the duplicate tests"


@dataclass(frozen=True)
class Snapshot:
    """The statically collected tests of one commit."""

    fingerprints: dict[str, str]
    behaviours: dict[str, str | None]
    errors: dict[str, str]


class CollectedCase(Protocol):
    """One statically collected test, as an adapter's ``cases.collect`` reports it."""

    @property
    def selector(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...


class CollectedFile(Protocol):
    """The statically collected tests of one file."""

    @property
    def cases(self) -> Sequence[CollectedCase]: ...

    @property
    def behaviours(self) -> Mapping[str, str | None]: ...


Collector = Callable[[str, str], CollectedFile]


@dataclass(frozen=True)
class FrameworkCollector:
    """One declared adapter's static collector and the test files it reads."""

    name: str
    locations: tuple[str, ...]
    suffixes: tuple[str, ...]
    collect: Collector


def framework_collectors(profile: dict[str, Any]) -> list[FrameworkCollector]:
    """Each declared adapter's ``cases.collect`` and ``cases.SUFFIXES``; ValueError when one lacks them."""
    found = []
    for name in adapters.names(profile):
        if not adapters.installed(name):
            raise ValueError(f"adapter {name!r} is not installed")
        try:
            module = importlib.import_module(f"{adapters.module_name(name)}.cases")
        except ModuleNotFoundError as error:
            raise ValueError(
                f"adapter {name!r} has no static test-case collector"
            ) from error
        found.append(
            FrameworkCollector(
                name,
                tuple(adapters.locations(profile, name)),
                tuple(module.SUFFIXES),
                module.collect,
            )
        )
    return found


def adapter_collector(profile: dict[str, Any]) -> Collector:
    """Collect a test file with the one adapter whose locations and file types cover it."""
    frameworks = framework_collectors(profile)

    def collect(path: str, text: str) -> CollectedFile:
        owners = [
            framework
            for framework in frameworks
            if matches_any(path, list(framework.locations))
            and path.endswith(framework.suffixes)
        ]
        if len(owners) != 1:
            named = [framework.name for framework in owners]
            raise ValueError(
                "no adapter's locations and file types cover this test file"
                if not owners
                else f"adapters {named} all cover this test file; give them disjoint locations"
            )
        return owners[0].collect(path, text)

    return collect


def test_blobs(repo: Path, ref: str, profile: dict[str, Any]) -> dict[str, str]:
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
    repo: Path,
    blobs: Mapping[str, str],
    collect: Collector,
    cache: dict[str, CollectedFile | ValueError],
) -> Snapshot:
    """Collect each file once per blob, so files unchanged between commits parse once."""
    fingerprints: dict[str, str] = {}
    behaviours: dict[str, str | None] = {}
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
            continue
        behaviours.update(result.behaviours)
        fingerprints.update((case.selector, case.fingerprint) for case in result.cases)
    return Snapshot(fingerprints, behaviours, errors)


def growth_findings(base: Snapshot, head: Snapshot) -> list[Finding]:
    found = []
    for selector in sorted(head.behaviours.keys() - base.behaviours.keys()):
        text = head.behaviours[selector]
        words = re.findall(r"[A-Za-z]+", text or "")
        meaningful = [
            word
            for word in words
            if word.lower()
            not in {"test", "case", "todo", "tbd", "placeholder", "example"}
        ]
        if not is_missing_text(text) and len(meaningful) >= 2:
            continue
        path = selector.split("::", 1)[0]
        found.append(
            Finding(
                GROWTH,
                path,
                f"{selector} has no descriptive behavior name",
                BEHAVIOUR_FIX,
            )
        )
    return found


def duplicate_findings(
    base: Mapping[str, str], head: Mapping[str, str]
) -> list[Finding]:
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
        Finding(
            GROWTH, path, f"cannot collect tests statically: {error}", BEHAVIOUR_FIX
        )
        for path, error in sorted(head.errors.items())
        if path in changed
    ]


def check(repo: Path, profile: dict[str, Any], diff: DiffRange) -> list[Finding]:
    changes = changed_files(repo, diff)
    changed_paths = {path for change in changes for path in change.paths}
    if not any(is_test_file(path, profile) for path in changed_paths):
        return []
    try:
        collect = adapter_collector(profile)
    except ValueError as error:
        return [
            Finding(
                GROWTH,
                PROFILE_PATH,
                str(error),
                "declare installed adapters that ship 'cases.collect' and 'cases.SUFFIXES'",
            )
        ]
    cache: dict[str, CollectedFile | ValueError] = {}
    base = snapshot(repo, test_blobs(repo, diff.base, profile), collect, cache)
    head = snapshot(repo, test_blobs(repo, diff.head, profile), collect, cache)
    found = collection_findings(head, changed_paths)
    found.extend(growth_findings(base, head))
    found.extend(duplicate_findings(base.fingerprints, head.fingerprints))
    return found


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    """``guardrails diff-check --base <ref> [--head <ref>]``."""
    try:
        found = check(repo, profile, parse_range(argv))
    except DiffError as error:
        return [range_finding(GROWTH, error)]
    return apply_exceptions(found, profile)
