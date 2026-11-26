from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.context.config.adapter_declarations import tier_locations
from guardrails.context.config.reader import tests_settings
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any
from guardrails.foundation.source.modules import folder_of
from guardrails.foundation.source.tree import SourceError, parse

if TYPE_CHECKING:
    from ast_grep_py import SgNode

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


class CodeTree:
    def __init__(
        self,
        inventory: Iterable[str],
        extensions: tuple[str, ...],
        manifests: frozenset[str],
    ) -> None:
        self.code: set[str] = set()
        self.bundles: set[str] = set()
        for path in inventory:
            pure = PurePosixPath(path)
            folder = pure.parent.as_posix()
            if folder == ".":
                continue
            if pure.name in manifests:
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


class PackageTree(CodeTree):
    def __init__(self, inventory: Iterable[str]) -> None:
        python = conventions.load_pack("python")
        paths = list(inventory)
        super().__init__(paths, tuple(python["extensions"]), BUNDLE_MANIFESTS)
        self.packages = {
            PurePosixPath(path).parent.as_posix()
            for path in paths
            if PurePosixPath(path).name == python["package_marker"]
        } - {"."}

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


class Sources:
    def __init__(self, root: Path, inventory: Iterable[str]) -> None:
        self.root = root
        self.folders: dict[str, list[str]] = {}
        for path in inventory:
            self.folders.setdefault(folder_of(path), []).append(path)
        self.trees: dict[str, SgNode] = {}

    def beside(self, folder: str, extensions: tuple[str, ...]) -> list[str]:
        return [
            path for path in self.folders.get(folder, []) if path.endswith(extensions)
        ]

    def text(self, path: str) -> str:
        try:
            return (self.root / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise SourceError(f"cannot read {path}: {error}") from error

    def tree(self, path: str) -> SgNode:
        if path not in self.trees:
            self.trees[path] = parse(path, self.text(path))
        return self.trees[path]


def unreadable(rule: str, path: str, error: SourceError) -> Finding:
    return Finding(
        rule,
        path,
        str(error),
        "fix the file so it reads and parses; the placement check reads it",
    )


def data_finding(path: str, folder: str) -> Finding:
    return Finding(
        RUNTIME_DATA,
        path,
        f"runtime data file inside code folder {folder}/",
        "move it to a root data folder outside the code and read it from there",
    )


def runtime_data_findings(path: str, tree: PackageTree) -> list[Finding]:
    if PurePosixPath(path).suffix not in DATA_SUFFIXES:
        return []
    folder = tree.enclosing(path)
    if folder is None:
        return []
    return [data_finding(path, folder)]


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
