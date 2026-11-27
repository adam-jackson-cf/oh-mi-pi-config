"""Which files of a source tree import each source file, in every structure language: the
import graph the candidate proposers read (structure-languages.json proposers.signals
importers)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from guardrails.foundation.source import go, modules, python, typescript
from guardrails.foundation.source.tree import SourceError, language_of

if TYPE_CHECKING:
    from ast_grep_py import SgNode

GO_MODULE = "go.mod"
MAX_IMPORT_TEXT = 240


@dataclass(frozen=True)
class Importer:
    """A file that imports another, with the import as it reads on its line."""

    path: str
    text: str


Importers = dict[str, list[Importer]]


def line_text(source: str, number: int) -> str:
    lines = source.splitlines()
    return (
        lines[number - 1].strip()[:MAX_IMPORT_TEXT] if 0 < number <= len(lines) else ""
    )


def script_importers(tree: Mapping[str, str], roots: Mapping[str, SgNode]) -> Importers:
    """Each parsed TypeScript file's importers among the parsed files: each import
    resolves as the import rules resolve it (modules.ScriptResolver over the tree, its
    tsconfig.json files included), once per importer."""
    resolver = modules.ScriptResolver(set(tree), tree.__getitem__)
    found: Importers = defaultdict(list)
    for path in sorted(roots):
        if language_of(path) != "typescript":
            continue
        seen: set[str] = set()
        for item in typescript.imports(roots[path]):
            target = resolver.resolve(path, item.specifier)
            if (
                target is None
                or target == path
                or language_of(target) != "typescript"
                or target in seen
            ):
                continue
            seen.add(target)
            found[target].append(Importer(path, line_text(tree[path], item.line)))
    return found


@dataclass
class GoModules:
    """The module each Go file belongs to: its nearest go.mod, read once."""

    tree: Mapping[str, str]
    paths: dict[str, str]

    def module(self, path: str) -> tuple[str, str] | None:
        """The module path and folder of a file, or None outside every module."""
        manifest = modules.nearest(path, GO_MODULE, self.tree)
        if manifest is None:
            return None
        if manifest not in self.paths:
            try:
                self.paths[manifest] = modules.go_module_path(self.tree[manifest])
            except SourceError as error:
                raise SourceError(f"{manifest}: {error}") from error
        return self.paths[manifest], modules.folder_of(manifest)


def package_members(roots: Mapping[str, SgNode]) -> dict[str, list[str]]:
    """The built non-test Go files of each folder: the files of its package."""
    members: dict[str, list[str]] = defaultdict(list)
    for path in sorted(roots):
        if (
            language_of(path) == "go"
            and go.built(path, roots[path])
            and not go.is_test_file(path)
        ):
            members[modules.folder_of(path)].append(path)
    return members


def go_importers(tree: Mapping[str, str], roots: Mapping[str, SgNode]) -> Importers:
    """Each built Go file's importers among the parsed files: an import of a package of
    the importer's module names every file of that package; a file outside every module
    imports no repository package."""
    members = package_members(roots)
    owners = GoModules(tree, {})
    found: Importers = defaultdict(list)
    for path in sorted(roots):
        root = roots[path]
        if language_of(path) != "go" or not go.built(path, root):
            continue
        module = owners.module(path)
        if module is None:
            continue
        own, seen = modules.folder_of(path), set()
        for node, value in go.import_paths(root):
            folder = modules.package_folder(*module, value)
            if folder is None or folder == own or folder in seen:
                continue
            seen.add(folder)
            for target in members.get(folder, []):
                found[target].append(Importer(path, f"import {node.text()}"))
    return found


def python_importers(
    files: Collection[str], tree: Mapping[str, str], roots: Mapping[str, SgNode]
) -> Importers:
    """Each Python file's importers among the parsed files: each import statement
    resolves to the files modules.PythonIndex names over files, once per importer."""
    index = modules.PythonIndex(path for path in files if language_of(path) == "python")
    found: Importers = defaultdict(list)
    for path in sorted(roots):
        if language_of(path) != "python":
            continue
        seen: set[str] = set()
        for item in python.imports(roots[path]):
            for target in index.resolve(path, item):
                if target not in seen:
                    seen.add(target)
                    found[target].append(
                        Importer(path, line_text(tree[path], item.line))
                    )
    return found


def graph(
    files: Collection[str], tree: Mapping[str, str], roots: Mapping[str, SgNode]
) -> Importers:
    """Each file's importers in its own language: Python, TypeScript and Go."""
    found: Importers = defaultdict(list)
    for part in (
        python_importers(files, tree, roots),
        script_importers(tree, roots),
        go_importers(tree, roots),
    ):
        for target, items in part.items():
            found[target].extend(items)
    return found


def manifests(languages: Collection[str]) -> frozenset[str]:
    """The files import resolution reads besides source: tsconfig.json for TypeScript,
    go.mod for Go."""
    names = {"typescript": modules.TSCONFIG, "go": GO_MODULE}
    return frozenset(names[language] for language in languages if language in names)
