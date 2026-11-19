"""Whole-repository Python import graph for the configured tier packages.

The first component below a tier is a part (a module or an owning subpackage).
Descendants of one subpackage are one owner. All populated tiers except the highest
configured tier are shared tiers; the highest tier drives rather than serves others.
Readers are distinct parts, not import statements or files. Only scanned Python
files contribute edges, with the same package and source-root rules as path-check.
"""

from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches,
    matches_any,
    repo_paths,
)

TIER = "STRUCT.tier-readership"
OWNER = "STRUCT.single-owner-placement"
SIBLING = "STRUCT.sibling-isolation"
DIRECTION = "STRUCT.dependency-direction"


def import_config(profile: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    structure = profile.get("structure")
    structure = structure if isinstance(structure, dict) else {}
    direction = structure.get("import_direction")
    return (direction if isinstance(direction, dict) else {}), as_list(
        structure.get("tiers")
    )


def imported_edges(tree: ast.Module, module: str, package: str) -> list[str]:
    """Resolve package-local imports without importing authored code."""
    parent = (
        module.removesuffix(".__init__")
        if module.endswith(".__init__")
        else module.rpartition(".")[0]
    )
    edges: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            destination = node.module or ""
            if node.level:
                destination = importlib.util.resolve_name(
                    "." * node.level + destination, parent
                )
            if destination:
                edges.extend(
                    destination if alias.name == "*" else f"{destination}.{alias.name}"
                    for alias in node.names
                )
    return [edge for edge in edges if edge == package or edge.startswith(f"{package}.")]


def part(module: str, package: str, tiers: list[str]) -> tuple[str, str] | None:
    components = module.removeprefix(f"{package}.").split(".")
    if len(components) < 2 or components[0] not in tiers or components[1] == "__init__":
        return None
    return components[0], components[1]


@dataclass
class Graph:
    """Scanned modules of the import-direction package and their package-local imports."""

    package: str
    root: str
    tiers: list[str]
    modules: dict[str, str] = field(default_factory=dict)
    edges: dict[str, list[str]] = field(default_factory=dict)

    def part(self, module: str) -> tuple[str, str] | None:
        return part(module, self.package, self.tiers)


def scan_modules(
    repo: Path, paths: list[str], scan: list[str], graph: Graph, found: list[Finding]
) -> None:
    for path in paths:
        if (
            not path.endswith(".py")
            or not matches_any(path, scan)
            or not (repo / path).is_file()
        ):
            continue
        relative = (
            PurePosixPath(path).relative_to(graph.root.rstrip("/"))
            if matches(path, graph.root)
            else None
        )
        module = (
            ".".join([graph.package, *relative.with_suffix("").parts])
            if relative
            else graph.package
        )
        try:
            graph.edges[path] = imported_edges(
                ast.parse((repo / path).read_bytes(), filename=path),
                module,
                graph.package,
            )
        except (OSError, SyntaxError, ValueError, ImportError) as error:
            found.append(
                Finding(DIRECTION, path, str(error), "restore readable Python imports")
            )
        if relative:
            graph.modules[module.removesuffix(".__init__")] = path


def resolved_module(edge: str, modules: dict[str, str]) -> str:
    destination = edge
    while destination not in modules and "." in destination:
        destination = destination.rpartition(".")[0]
    return destination


def reader_sets(
    graph: Graph, found: list[Finding]
) -> tuple[dict[str, set[tuple[str, str]]], dict[str, set[tuple[str, str]]]]:
    """Parts reading each module and each shared tier; sibling imports become findings."""
    readers: dict[str, set[tuple[str, str]]] = {
        module: set() for module in graph.modules
    }
    tier_readers: dict[str, set[tuple[str, str]]] = {
        tier: set() for tier in graph.tiers[:-1]
    }
    for module, path in sorted(graph.modules.items()):
        source = graph.part(module)
        if source is None:
            continue
        for edge in sorted(set(graph.edges.get(path, []))):
            destination = resolved_module(edge, graph.modules)
            target = graph.part(destination)
            if target is None or target == source:
                continue
            if source[0] == target[0]:
                found.append(
                    Finding(
                        SIBLING,
                        path,
                        f"sibling import {edge}",
                        "move shared code to the lowest tier both siblings may read",
                    )
                )
            if graph.tiers.index(source[0]) > graph.tiers.index(target[0]):
                readers[destination].add(source)
                tier_readers[target[0]].add(source)
    return readers, tier_readers


def tier_findings(
    graph: Graph, tier_readers: dict[str, set[tuple[str, str]]]
) -> list[Finding]:
    found = []
    for tier, users in tier_readers.items():
        populated = any(
            owner is not None and owner[0] == tier
            for module in graph.modules
            for owner in [graph.part(module)]
        )
        if populated and len(users) < 2:
            found.append(
                Finding(
                    TIER,
                    f"{graph.root.rstrip('/')}/{tier}",
                    f"tier is read by {len(users)} other parts",
                    "keep modules flat until at least two parts read the tier",
                )
            )
    return found


def owner_findings(
    graph: Graph,
    readers: dict[str, set[tuple[str, str]]],
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
                    f"move this module under {graph.root.rstrip('/')}/{tier}/{name}",
                )
            )
    return found


def run(
    repo: Path,
    profile: dict[str, Any],
    argv: list[str],
    *,
    inventory: list[str] | None = None,
) -> list[Finding]:
    direction, tiers = import_config(profile)
    package, root = direction.get("package"), direction.get("source_root")
    if not (isinstance(package, str) and isinstance(root, str) and tiers):
        return []
    try:
        paths = repo_paths(repo) if inventory is None else inventory
    except InventoryError as error:
        return [Finding(DIRECTION, ".", str(error), "run inside a Git work tree")]
    graph = Graph(package, root, tiers)
    found: list[Finding] = []
    scan_modules(repo, paths, as_list(direction.get("scan")) or [root], graph, found)
    readers, tier_readers = reader_sets(graph, found)
    found.extend(tier_findings(graph, tier_readers))
    found.extend(owner_findings(graph, readers, tier_readers))
    return apply_exceptions(found, profile)
