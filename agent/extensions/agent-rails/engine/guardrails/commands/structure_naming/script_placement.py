from __future__ import annotations

import json
from collections.abc import Collection, Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming import typescript
from guardrails.commands.structure_naming.package_placement import (
    DATA_SUFFIXES,
    ENTRY_POINT,
    RUNTIME_DATA,
    TOP_TIER_DEPTH,
    CodeTree,
    Sources,
    data_finding,
    held_by_tests,
    unreadable,
)
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.source.modules import folder_of, joined
from guardrails.foundation.source.tree import SourceError, languages

BUNDLE_MANIFESTS = frozenset({"SKILL.md", "package.json"})
PACKAGE_MANIFEST = "package.json"
RELATIVE_PREFIXES = ("./", "../")


def extensions() -> tuple[str, ...]:
    return languages()["typescript"]


def imported_beside(path: str, sources: Sources) -> bool:
    folder = folder_of(path)
    return any(
        item.specifier.startswith(RELATIVE_PREFIXES)
        and joined(folder, item.specifier) == path
        for source in sources.beside(folder, extensions())
        for item in typescript.imports(sources.tree(source))
    )


def runtime_data_findings(path: str, tree: CodeTree, sources: Sources) -> list[Finding]:
    if PurePosixPath(path).suffix not in DATA_SUFFIXES or conventions.fixed_file(path):
        return []
    folder = tree.enclosing(path)
    if folder is None:
        return []
    try:
        if imported_beside(path, sources):
            return []
    except SourceError as error:
        return [unreadable(RUNTIME_DATA, path, error)]
    return [data_finding(path, folder)]


def manifest_entries(manifest: str, text: str) -> list[tuple[str, str]]:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SourceError(f"cannot parse {manifest}: {error}") from error
    if not isinstance(data, dict):
        raise SourceError(f"{manifest} must hold a JSON object")
    found: list[tuple[str, str]] = []
    commands, main = data.get("bin"), data.get("main")
    if isinstance(commands, str):
        found.append(("bin", commands))
    elif isinstance(commands, dict) and all(
        isinstance(value, str) for value in commands.values()
    ):
        found.extend(("bin", value) for value in commands.values())
    elif commands is not None:
        raise SourceError(f"{manifest}: bin must be a path or a map of names to paths")
    if isinstance(main, str):
        found.append(("main", main))
    elif main is not None:
        raise SourceError(f"{manifest}: main must be a path")
    return found


def manifests_above(path: str, tracked: Collection[str]) -> list[str]:
    folders = [parent.as_posix() for parent in PurePosixPath(path).parents]
    return [
        manifest
        for folder in folders
        if (manifest := PurePosixPath(folder, PACKAGE_MANIFEST).as_posix()) in tracked
    ]


def entry_finding(entry: str, manifest: str, field: str) -> list[Finding]:
    package = folder_of(manifest)
    depth = len(PurePosixPath(entry).parent.relative_to(package).parts)
    if depth <= TOP_TIER_DEPTH:
        return []
    top = "the package root" if package == "." else f"{package}/"
    return [
        Finding(
            ENTRY_POINT,
            entry,
            f"{manifest} {field} entry {PurePosixPath(entry).name} sits {depth} "
            f"folders below {top}, below its top tier",
            f"move it to {top} or a folder directly under it and update {field} in "
            f"{manifest}, or record an owner-approved exceptions entry for an "
            "internal tool",
        )
    ]


def entry_point_findings(
    path: str, tracked: Collection[str], sources: Sources, config: dict[str, Any]
) -> list[Finding]:
    found: list[Finding] = []
    for manifest in manifests_above(path, tracked):
        try:
            entries = manifest_entries(manifest, sources.text(manifest))
        except SourceError as error:
            found.append(unreadable(ENTRY_POINT, manifest, error))
            continue
        for field, value in entries:
            entry = joined(folder_of(manifest), value)
            if (
                entry is not None
                and entry in tracked
                and path in {entry, manifest}
                and not held_by_tests(entry, config)
            ):
                found.extend(entry_finding(entry, manifest, field))
    return found


def script_file_findings(
    root: Path,
    paths: Iterable[str],
    inventory: Iterable[str],
    config: dict[str, Any],
) -> list[Finding]:
    tracked = set(inventory)
    tree = CodeTree(tracked, extensions(), BUNDLE_MANIFESTS)
    sources = Sources(root, tracked)
    found: list[Finding] = []
    for path in paths:
        found.extend(entry_point_findings(path, tracked, sources, config))
        if not held_by_tests(path, config):
            found.extend(runtime_data_findings(path, tree, sources))
    return found
