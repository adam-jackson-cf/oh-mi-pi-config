from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.reader import structure_settings
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches,
    matches_any,
    repo_paths,
)
from guardrails.foundation.source import go, modules, python, typescript
from guardrails.foundation.source.tree import SourceError, language_of, parse, read

if TYPE_CHECKING:
    from ast_grep_py import SgNode

TIER = "STRUCT.tier-readership"
OWNER = "STRUCT.single-owner-placement"
SIBLING = "STRUCT.sibling-isolation"
DIRECTION = "STRUCT.dependency-direction"
LANGUAGE_NAMES = {"python": "Python", "typescript": "TypeScript", "go": "Go"}
GO_MOD = "go.mod"

Key = tuple[str, str]


def import_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    structure = structure_settings(config)
    direction = structure.get("import_direction")
    return (direction if isinstance(direction, dict) else {}), as_list(
        structure.get("tiers")
    )


@dataclass(frozen=True)
class ImportSettings:
    package: str | None
    root: str
    tiers: list[str]
    scan: list[str]


def import_settings(config: dict[str, Any]) -> ImportSettings | None:
    direction, tiers = import_config(config)
    package, root = direction.get("package"), direction.get("source_root")
    if not isinstance(root, str):
        return None
    return ImportSettings(
        package if isinstance(package, str) else None,
        root.rstrip("/"),
        tiers,
        as_list(direction.get("scan")) or [root],
    )


def graph_language(path: str) -> str | None:
    language = language_of(path)
    if language == "go" and (go.is_test_file(path) or not go.built_path(path)):
        return None
    return language


@dataclass(frozen=True)
class Edge:
    label: str
    tier: str | None
    targets: tuple[Key, ...]


@dataclass(frozen=True)
class Unit:
    key: Key
    components: tuple[str, ...]
    path: str


@dataclass(frozen=True)
class RepoFiles:
    repo: Path

    def __contains__(self, path: object) -> bool:
        return isinstance(path, str) and (self.repo / path).is_file()


def first(components: tuple[str, ...]) -> str | None:
    return components[0] if components else None


def python_components(module: str, package: str) -> tuple[str, ...]:
    return tuple(module.removeprefix(f"{package}.").split("."))


def python_edge(edge: str, package: str) -> Edge:
    parts = edge.split(".")
    return Edge(
        edge,
        python_components(edge, package)[0],
        tuple(("python", ".".join(parts[:size])) for size in range(len(parts), 0, -1)),
    )


def script_components(relative: PurePosixPath) -> tuple[str, ...]:
    parts = relative.with_suffix("").parts
    return parts[:-1] if parts[-1:] == ("index",) else parts


@dataclass
class Reader:
    repo: Path
    settings: ImportSettings
    go_modules: dict[str, str] = field(default_factory=dict)
    scripts: modules.ScriptResolver = field(init=False)

    def __post_init__(self) -> None:
        self.scripts = modules.ScriptResolver(self.files, self.text)

    @property
    def files(self) -> RepoFiles:
        return RepoFiles(self.repo)

    def text(self, path: str) -> str:
        return read(self.repo, path)

    def source(self, path: str) -> SgNode:
        return parse(path, self.text(path))

    def relative(self, path: str) -> PurePosixPath | None:
        root = self.settings.root
        return PurePosixPath(path).relative_to(root) if matches(path, root) else None

    def go_module(self, path: str) -> str:
        if path not in self.go_modules:
            try:
                self.go_modules[path] = modules.go_module_path(self.text(path))
            except SourceError as error:
                raise SourceError(f"{path}: {error}") from error
        return self.go_modules[path]

    def python_module(self, path: str, package: str) -> modules.PythonModule:
        relative = self.relative(path)
        if relative is None:
            return modules.PythonModule(package, is_package=False)
        return modules.python_module(relative.as_posix(), package)

    def python_edges(self, path: str, package: str) -> list[Edge]:
        module = self.python_module(path, package)
        edges = modules.python_edges(python.imports(self.source(path)), module, package)
        return [python_edge(edge, package) for edge in edges]

    def script_edges(self, path: str) -> list[Edge]:
        tree = self.source(path)
        self.scripts.importer_config(path)  # an unreadable tsconfig fails every file
        found = []
        for item in typescript.imports(tree):
            target = self.scripts.resolve(path, item.specifier)
            relative = None if target is None else self.relative(target)
            if target is not None and relative is not None:
                found.append(
                    Edge(
                        item.specifier,
                        first(script_components(relative)),
                        (("typescript", target),),
                    )
                )
        return found

    def go_edges(self, path: str) -> list[Edge]:
        tree = self.source(path)
        if go.build_ignored(tree):
            return []
        manifest = modules.nearest(path, GO_MOD, self.files)
        if manifest is None:
            raise SourceError(f"no {GO_MOD} at or above {path}")
        module_path = self.go_module(manifest)
        module_folder = modules.folder_of(manifest)
        found = []
        for item in go.imports(tree):
            folder = modules.package_folder(module_path, module_folder, item.path)
            relative = None if folder is None else self.relative(folder)
            if folder is not None and relative is not None:
                found.append(Edge(item.path, first(relative.parts), (("go", folder),)))
        return found

    def edges(self, path: str, language: str) -> list[Edge] | Finding:
        package = self.settings.package
        if language == "python" and package is None:
            return Finding(
                DIRECTION,
                path,
                "structure.import_direction.package is not set",
                "name the Python package at source_root in "
                "structure.import_direction.package",
            )
        try:
            if package is not None and language == "python":
                return self.python_edges(path, package)
            if language == "typescript":
                return self.script_edges(path)
            return self.go_edges(path)
        except (OSError, ValueError) as error:
            return Finding(
                DIRECTION,
                path,
                str(error),
                f"restore readable {LANGUAGE_NAMES[language]} imports",
            )

    def unit(self, path: str, language: str) -> Unit | None:
        relative = self.relative(path)
        package = self.settings.package
        if relative is None:
            return None
        if language == "python":
            if package is None:
                return None
            module = modules.python_module(relative.as_posix(), package).name
            return Unit(("python", module), python_components(module, package), path)
        if language == "typescript":
            return Unit(("typescript", path), script_components(relative), path)
        folder = modules.folder_of(path)
        return Unit(("go", folder), relative.parent.parts, folder)


def part(components: tuple[str, ...], tiers: list[str]) -> tuple[str, str] | None:
    if len(components) < 2 or components[0] not in tiers or components[1] == "__init__":
        return None
    return components[0], components[1]


@dataclass
class Graph:
    root: str
    tiers: list[str]
    modules: dict[Key, str] = field(default_factory=dict)
    components: dict[Key, tuple[str, ...]] = field(default_factory=dict)
    edges: dict[Key, list[tuple[str, Edge]]] = field(
        default_factory=lambda: defaultdict(list)
    )

    def part(self, key: Key) -> tuple[str, str] | None:
        return part(self.components[key], self.tiers)

    def resolved(self, edge: Edge) -> Key | None:
        return next((key for key in edge.targets if key in self.modules), None)


def scan_modules(
    reader: Reader, paths: list[str], graph: Graph, found: list[Finding]
) -> None:
    for path in paths:
        language = graph_language(path)
        if (
            language is None
            or not matches_any(path, reader.settings.scan)
            or not (reader.repo / path).is_file()
        ):
            continue
        edges = reader.edges(path, language)
        if isinstance(edges, Finding):
            found.append(edges)
        unit = reader.unit(path, language)
        if unit is None:
            continue
        graph.modules[unit.key] = unit.path
        graph.components[unit.key] = unit.components
        if not isinstance(edges, Finding):
            graph.edges[unit.key].extend((path, edge) for edge in edges)


def reader_sets(
    graph: Graph, found: list[Finding]
) -> tuple[dict[Key, set[tuple[str, str]]], dict[str, set[tuple[str, str]]]]:
    readers: dict[Key, set[tuple[str, str]]] = {
        module: set() for module in graph.modules
    }
    tier_readers: dict[str, set[tuple[str, str]]] = {
        tier: set() for tier in graph.tiers[:-1]
    }
    for module in sorted(graph.modules):
        source = graph.part(module)
        if source is None:
            continue
        imports = sorted(
            set(graph.edges.get(module, [])), key=lambda item: (item[1].label, item[0])
        )
        for importer, edge in imports:
            destination = graph.resolved(edge)
            target = None if destination is None else graph.part(destination)
            if destination is None or target is None or target == source:
                continue
            if source[0] == target[0]:
                found.append(
                    Finding(
                        SIBLING,
                        importer,
                        f"sibling import {edge.label}",
                        "move shared code to the lowest tier both siblings may read",
                    )
                )
            if graph.tiers.index(source[0]) > graph.tiers.index(target[0]):
                readers[destination].add(source)
                tier_readers[target[0]].add(source)
    return readers, tier_readers


def tier_populated(graph: Graph, tier: str) -> bool:
    for module in graph.modules:
        owner = graph.part(module)
        if owner is not None and owner[0] == tier:
            return True
    return False


def tier_findings(
    graph: Graph, tier_readers: dict[str, set[tuple[str, str]]]
) -> list[Finding]:
    found = []
    for tier, users in tier_readers.items():
        if tier_populated(graph, tier) and len(users) < 2:
            found.append(
                Finding(
                    TIER,
                    f"{graph.root}/{tier}",
                    f"tier is read by {len(users)} other parts",
                    "keep modules flat until at least two parts read the tier",
                )
            )
    return found


def owner_findings(
    graph: Graph,
    readers: dict[Key, set[tuple[str, str]]],
    tier_readers: dict[str, set[tuple[str, str]]],
) -> list[Finding]:
    found = []
    for module, users in sorted(readers.items()):
        owner = graph.part(module)
        if owner and owner[0] in tier_readers and len(users) == 1:
            tier, name = next(iter(users))
            found.append(
                Finding(
                    OWNER,
                    graph.modules[module],
                    f"shared module is read only by {tier}.{name}",
                    f"move this module under {graph.root}/{tier}/{name}",
                )
            )
    return found


def import_graph_findings(
    repo: Path,
    config: dict[str, Any],
    *,
    inventory: list[str] | None = None,
) -> list[Finding]:
    settings = import_settings(config)
    if settings is None or not settings.tiers:
        return []
    try:
        paths = repo_paths(repo) if inventory is None else inventory
    except InventoryError as error:
        return [Finding(DIRECTION, ".", str(error), "run inside a Git work tree")]
    graph = Graph(settings.root, settings.tiers)
    found: list[Finding] = []
    scan_modules(Reader(repo, settings), paths, graph, found)
    readers, tier_readers = reader_sets(graph, found)
    found.extend(tier_findings(graph, tier_readers))
    found.extend(owner_findings(graph, readers, tier_readers))
    return apply_exceptions(found, config)
