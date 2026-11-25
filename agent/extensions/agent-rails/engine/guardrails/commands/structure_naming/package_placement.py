from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import Any

from guardrails.context.config.adapter_declarations import tier_locations
from guardrails.context.config.reader import tests_settings
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any

RUNTIME_DATA = "STRUCT.runtime-data-placement"
ENTRY_POINT = "STRUCT.entry-point-placement"
DATA_SUFFIXES = frozenset({
    ".cfg",
    ".hbs",
    ".ini",
    ".j2",
    ".jinja",
    ".jinja2",
    ".json",
    ".jsonl",
    ".mustache",
    ".tmpl",
    ".toml",
    ".tpl",
    ".yaml",
    ".yml",
})
ENTRY_MODULES = frozenset({"__main__.py", "cli.py"})
BUNDLE_MANIFESTS = frozenset({"SKILL.md", "pyproject.toml", "setup.cfg", "setup.py"})
TOP_TIER_DEPTH = 1


class PackageTree:
    def __init__(self, inventory: Iterable[str]) -> None:
        python = conventions.load_pack("python")
        marker, extensions = python["package_marker"], tuple(python["extensions"])
        self.packages: set[str] = set()
        self.code: set[str] = set()
        self.bundles: set[str] = set()
        for path in inventory:
            pure = PurePosixPath(path)
            folder = pure.parent.as_posix()
            if folder == ".":
                continue
            if pure.name == marker:
                self.packages.add(folder)
            if pure.name in BUNDLE_MANIFESTS:
                self.bundles.add(folder)
            elif pure.name.endswith(extensions):
                self.code.add(folder)

    def enclosing(self, path: str) -> str | None:
        for parent in PurePosixPath(path).parents:
            folder = parent.as_posix()
            if folder in self.code:
                return folder
            if folder in self.bundles:
                return None
        return None

    def root(self, folder: str) -> str:
        current = PurePosixPath(folder)
        while current.parent.as_posix() in self.packages:
            current = current.parent
        return current.as_posix()


def held_by_tests(path: str, config: dict[str, Any]) -> bool:
    placement = conventions.load_pack("python")["placement"]
    folders = {placement["root"], placement["fixtures_dir"]}
    if folders.intersection(PurePosixPath(path).parts[:-1]):
        return True
    layout = tests_settings(config, "layout")
    holders = (
        as_list([layout.get("fixtures_dir"), layout.get("support_dir")])
        if isinstance(layout, dict)
        else []
    )
    locations = [item for items in tier_locations(config).values() for item in items]
    return matches_any(path, [*holders, *locations])


def runtime_data_findings(path: str, tree: PackageTree) -> list[Finding]:
    if PurePosixPath(path).suffix not in DATA_SUFFIXES:
        return []
    folder = tree.enclosing(path)
    if folder is None:
        return []
    return [
        Finding(
            RUNTIME_DATA,
            path,
            f"runtime data file inside code folder {folder}/",
            "move it to a root data folder outside the code and read it from there",
        )
    ]


def entry_point_findings(path: str, tree: PackageTree) -> list[Finding]:
    pure = PurePosixPath(path)
    folder = pure.parent.as_posix()
    if pure.name not in ENTRY_MODULES or folder not in tree.packages:
        return []
    root = tree.root(folder)
    depth = len(pure.parent.relative_to(root).parts)
    if depth <= TOP_TIER_DEPTH:
        return []
    return [
        Finding(
            ENTRY_POINT,
            path,
            f"entry module {pure.name} sits {depth} folders deep in package {root}/, "
            "below its top tier",
            f"move it to {root}/ or a folder directly under it, or record an "
            "owner-approved exceptions entry for an internal tool",
        )
    ]


def package_file_findings(
    paths: Iterable[str], inventory: Iterable[str], config: dict[str, Any]
) -> list[Finding]:
    tree = PackageTree(inventory)
    found: list[Finding] = []
    for path in paths:
        if held_by_tests(path, config):
            continue
        found.extend(runtime_data_findings(path, tree))
        found.extend(entry_point_findings(path, tree))
    return found
