"""Which Go packages the config's public entry points own, and the import paths that reach
their folders, through the go.mod each file sits under."""

from __future__ import annotations

import posixpath
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from guardrails.commands.adapters.owned_source import READ_ERRORS, manifest_finding
from guardrails.context.config.entry_points import EntryPoint
from guardrails.foundation.findings import Finding
from guardrails.foundation.source.modules import (
    folder_of,
    go_module_path,
    nearest,
    package_folder,
    package_import_path,
)
from guardrails.foundation.source.tree import read

GO_MOD = "go.mod"


@dataclass
class GoModules:
    """The Go modules of a repository, each go.mod read once; an unreadable go.mod is a
    finding and its files have no module."""

    repo: Path
    tracked: Collection[str]
    names: dict[str, str | None] = field(default_factory=dict)
    problems: dict[str, Finding] = field(default_factory=dict)

    def manifest_module(self, manifest: str) -> tuple[str, str] | None:
        if manifest not in self.names:
            try:
                self.names[manifest] = go_module_path(read(self.repo, manifest))
            except READ_ERRORS as error:
                self.names[manifest] = None
                self.problems[manifest] = manifest_finding(manifest, error)
        name = self.names[manifest]
        return None if name is None else (folder_of(manifest), name)

    def module(self, path: str) -> tuple[str, str] | None:
        """The folder and module path of the go.mod at or above a file."""
        manifest = nearest(path, GO_MOD, self.tracked)
        return None if manifest is None else self.manifest_module(manifest)

    def folder(self, importer: str, import_path: str) -> str | None:
        """The folder of the package an import path names, from the importer's module."""
        module = self.module(importer)
        return (
            None
            if module is None
            else package_folder(module[1], module[0], import_path)
        )

    def import_path(self, folder: str) -> str | None:
        module = self.module(posixpath.join(folder, GO_MOD))
        return (
            None
            if module is None
            else package_import_path(module[1], module[0], folder)
        )

    def entry_folder(self, import_path: str) -> str | None:
        """The folder of the package an entry point's import path names, in any module."""
        manifests = sorted(
            path for path in self.tracked if PurePosixPath(path).name == GO_MOD
        )
        for manifest in manifests:
            module = self.manifest_module(manifest)
            found = (
                None
                if module is None
                else package_folder(module[1], module[0], import_path)
            )
            if found is not None:
                return found
        return None


def owned_roots(points: Iterable[EntryPoint]) -> tuple[str, ...]:
    """The owned package folders: each entry point's package as a folder path."""
    return tuple(
        sorted({posixpath.normpath(point.package.rstrip("/")) for point in points})
    )


def under(path: str, root: str) -> bool:
    return root == "." or path == root or path.startswith(root + "/")


def owned_imports(roots: Iterable[str], modules: GoModules) -> tuple[str, ...]:
    """The import path of each owned folder inside a Go module."""
    return tuple(
        sorted({path for root in roots if (path := modules.import_path(root))})
    )


def owned_import(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def owned_path(path: str, roots: tuple[str, ...]) -> bool:
    return any(under(path, root) for root in roots)
