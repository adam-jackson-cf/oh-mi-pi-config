from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.context.config.suite_files import is_test_source
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.source.modules import folder_of
from guardrails.foundation.source.tree import (
    SourceError,
    languages,
    parse,
    rule_definitions,
)

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
TOP_TIER_DEPTH = 1


def bundle_manifests(language: str) -> frozenset[str]:
    """The files that make a folder a distribution of a language (structure-languages.json)."""
    manifests = rule_definitions(RUNTIME_DATA)[language]["bundle_manifests"]
    return frozenset(manifests)


def entry_modules() -> frozenset[str]:
    """The Python module names that are a package's entry points."""
    return frozenset(rule_definitions(ENTRY_POINT)["python"]["entry_modules"])


class CodeTree:
    """The folders of one language's source and of its bundle manifests: where runtime
    data does not belong (inside code) and where it does (a distribution's own folder)."""

    def __init__(self, inventory: Iterable[str], language: str) -> None:
        extensions = languages()[language]
        self.manifests = bundle_manifests(language)
        self.code: set[str] = set()
        self.bundles: set[str] = set()
        for path in inventory:
            pure = PurePosixPath(path)
            folder = pure.parent.as_posix()
            if folder == ".":
                continue
            if pure.name in self.manifests:
                self.bundles.add(folder)
            elif pure.name.endswith(extensions):
                self.code.add(folder)

    def enclosing(self, path: str) -> str | None:
        """The nearest code folder above a file, unless a bundle folder (a folder with a
        bundle manifest) comes first: a distribution's own folder holds its manifests and
        data beside its code."""
        for parent in PurePosixPath(path).parents:
            folder = parent.as_posix()
            if folder in self.bundles:
                return None
            if folder in self.code:
                return folder
        return None

    def misplaced_data(self, path: str) -> str | None:
        """The code folder a runtime data file sits in, or None when the file is no
        runtime data (not a data suffix, a bundle manifest, a tooling fixed file such as
        pyproject.toml, ruff.toml or .golangci.yml, or in an ignored folder) or sits
        outside code."""
        pure = PurePosixPath(path)
        if (
            pure.suffix not in DATA_SUFFIXES
            or pure.name in self.manifests
            or conventions.fixed_file(path)
            or conventions.ignored_path(path)
        ):
            return None
        return self.enclosing(path)


class PackageTree(CodeTree):
    def __init__(self, inventory: Iterable[str]) -> None:
        python = conventions.load_pack("python")
        paths = list(inventory)
        super().__init__(paths, "python")
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
    folder = tree.misplaced_data(path)
    return [] if folder is None else [data_finding(path, folder)]


def entry_point_findings(path: str, tree: PackageTree) -> list[Finding]:
    pure = PurePosixPath(path)
    folder = pure.parent.as_posix()
    if pure.name not in entry_modules() or folder not in tree.packages:
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
        if is_test_source(path, config):
            continue
        found.extend(runtime_data_findings(path, tree))
        found.extend(entry_point_findings(path, tree))
    return found
