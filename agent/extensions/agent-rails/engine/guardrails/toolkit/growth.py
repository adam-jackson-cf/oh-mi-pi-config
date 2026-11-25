from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from guardrails.context.changes import DiffRange, changed_files, git
from guardrails.context.config import adapter_declarations
from guardrails.context.config.section_schema import is_missing_text
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation.findings import Finding, config_label
from guardrails.foundation.inventory import matches_any

GROWTH = "TEST.growth.justified"
DUPLICATE = "TEST.duplicate-path"
BEHAVIOUR_FIX = "name the test after the behavior it proves, or remove the test"
DUPLICATE_FIX = "merge or remove one of the duplicate tests"


@dataclass(frozen=True)
class Snapshot:
    fingerprints: dict[str, str]
    behaviours: dict[str, str | None]
    errors: dict[str, str]


class CollectedCase(Protocol):
    @property
    def selector(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...


class CollectedFile(Protocol):
    @property
    def cases(self) -> Sequence[CollectedCase]: ...

    @property
    def behaviours(self) -> Mapping[str, str | None]: ...


Collector = Callable[[str, str], CollectedFile]


@dataclass(frozen=True)
class FrameworkCollector:
    name: str
    locations: tuple[str, ...]
    suffixes: tuple[str, ...]
    collect: Collector


def framework_collectors(config: dict[str, Any]) -> list[FrameworkCollector]:
    found = []
    for name in adapter_declarations.names(config):
        if not adapter_declarations.installed(name):
            raise ValueError(f"adapter {name!r} is not installed")
        try:
            module = adapter_declarations.adapter_part(name, "cases")
        except ModuleNotFoundError as error:
            raise ValueError(
                f"adapter {name!r} has no static test-case collector"
            ) from error
        found.append(
            FrameworkCollector(
                name,
                tuple(adapter_declarations.locations(config, name)),
                tuple(module.SUFFIXES),
                module.collect,
            )
        )
    return found


def adapter_collector(config: dict[str, Any]) -> Collector:
    frameworks = framework_collectors(config)

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


def test_blobs(repo: Path, ref: str, config: dict[str, Any]) -> dict[str, str]:
    output = git(repo, "ls-tree", "-r", "-z", ref)
    blobs = {}
    for entry in output.split("\0"):
        if not entry or "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        kind, blob = meta.split()[1:3]
        if kind == "blob" and is_test_file(path, config):
            blobs[path] = blob
    return blobs


def snapshot(
    repo: Path,
    blobs: Mapping[str, str],
    collect: Collector,
    cache: dict[str, CollectedFile | ValueError],
) -> Snapshot:
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
    seen: dict[str, str] = {}
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


def check(repo: Path, config: dict[str, Any], diff: DiffRange) -> list[Finding]:
    changes = changed_files(repo, diff)
    changed_paths = {path for change in changes for path in change.paths}
    if not any(is_test_file(path, config) for path in changed_paths):
        return []
    try:
        collect = adapter_collector(config)
    except ValueError as error:
        return [
            Finding(
                GROWTH,
                config_label("tests"),
                str(error),
                "declare installed adapters that ship 'cases.collect' and 'cases.SUFFIXES'",
            )
        ]
    cache: dict[str, CollectedFile | ValueError] = {}
    base = snapshot(repo, test_blobs(repo, diff.base, config), collect, cache)
    head = snapshot(repo, test_blobs(repo, diff.head, config), collect, cache)
    found = collection_findings(head, changed_paths)
    found.extend(growth_findings(base, head))
    found.extend(duplicate_findings(base.fingerprints, head.fingerprints))
    return found
