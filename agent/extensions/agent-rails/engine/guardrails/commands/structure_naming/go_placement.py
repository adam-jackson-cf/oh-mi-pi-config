from __future__ import annotations

from collections.abc import Collection, Iterable
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming.package_placement import (
    DATA_SUFFIXES,
    ENTRY_POINT,
    RUNTIME_DATA,
    CodeTree,
    Sources,
    data_finding,
    held_by_tests,
    unreadable,
)
from guardrails.foundation.findings import Finding
from guardrails.foundation.source import go
from guardrails.foundation.source.modules import folder_of, nearest
from guardrails.foundation.source.tree import SourceError, languages

BUNDLE_MANIFESTS = frozenset({"SKILL.md", "go.mod"})
MODULE_MANIFEST = "go.mod"
TEST_DATA = "testdata"
COMMANDS = "cmd"
WALK_ALL = "all:"
HIDDEN_PREFIXES = (".", "_")
MAIN_PACKAGE = "main"


def extensions() -> tuple[str, ...]:
    return languages()["go"]


def in_test_data(path: str) -> bool:
    return TEST_DATA in PurePosixPath(path).parts[:-1]


def embedded(pattern: str, relative: str) -> bool:
    segments = pattern.removeprefix(WALK_ALL).split("/")
    names = relative.split("/")
    if len(names) < len(segments) or not all(
        fnmatchcase(name, segment)
        for name, segment in zip(names, segments, strict=False)
    ):
        return False
    below = names[len(segments) :]
    return pattern.startswith(WALK_ALL) or not any(
        name.startswith(HIDDEN_PREFIXES) for name in below
    )


def embedded_in_package(path: str, folder: str, sources: Sources) -> bool:
    relative = PurePosixPath(path).relative_to(folder).as_posix()
    return any(
        embedded(pattern, relative)
        for source in sources.beside(folder, extensions())
        for pattern in go.embed_patterns(sources.tree(source))
    )


def runtime_data_findings(path: str, tree: CodeTree, sources: Sources) -> list[Finding]:
    if PurePosixPath(path).suffix not in DATA_SUFFIXES or in_test_data(path):
        return []
    folder = tree.enclosing(path)
    if folder is None:
        return []
    try:
        if embedded_in_package(path, folder, sources):
            return []
    except SourceError as error:
        return [unreadable(RUNTIME_DATA, path, error)]
    return [data_finding(path, folder)]


def is_command(path: str, sources: Sources) -> bool:
    root = sources.tree(path)
    return go.package_name(root) == MAIN_PACKAGE and not go.build_ignored(root)


def top_tier(folder: str, module: str) -> bool:
    parts = PurePosixPath(folder).relative_to(module).parts
    return not parts or (len(parts) == 2 and parts[0] == COMMANDS)


def entry_point_findings(
    path: str, tracked: Collection[str], sources: Sources
) -> list[Finding]:
    if not path.endswith(extensions()) or go.is_test_file(path) or in_test_data(path):
        return []
    manifest = nearest(path, MODULE_MANIFEST, tracked)
    if manifest is None:
        return []
    try:
        if not is_command(path, sources):
            return []
    except SourceError as error:
        return [unreadable(ENTRY_POINT, path, error)]
    module, folder = folder_of(manifest), folder_of(path)
    if top_tier(folder, module):
        return []
    root = "" if module == "." else f"{module}/"
    return [
        Finding(
            ENTRY_POINT,
            path,
            f"package main file {PurePosixPath(path).name} sits in {folder}/, outside "
            f"the module root {root or './'} and {root}{COMMANDS}/<name>/",
            f"move the command to {root}{COMMANDS}/<name>/ or the module root, or "
            "record an owner-approved exceptions entry for an internal tool",
        )
    ]


def go_file_findings(
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
        if held_by_tests(path, config):
            continue
        found.extend(runtime_data_findings(path, tree, sources))
        found.extend(entry_point_findings(path, tracked, sources))
    return found
