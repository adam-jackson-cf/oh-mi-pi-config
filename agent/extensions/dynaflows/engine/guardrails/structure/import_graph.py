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
from pathlib import Path, PurePosixPath

from guardrails.config.house_style import apply_exceptions
from guardrails.findings import Finding
from guardrails.structure.inventory import InventoryError, as_list, matches, matches_any, repo_paths

TIER = "STRUCT.tier-readership"
OWNER = "STRUCT.single-owner-placement"
SIBLING = "STRUCT.sibling-isolation"
DIRECTION = "STRUCT.dependency-direction"


def import_config(profile: dict) -> tuple[dict, list[str]]:
    structure = profile.get("structure")
    structure = structure if isinstance(structure, dict) else {}
    direction = structure.get("import_direction")
    return (direction if isinstance(direction, dict) else {}), as_list(structure.get("tiers"))


def imported_edges(tree: ast.Module, module: str, package: str) -> list[str]:
    """Resolve package-local imports without importing authored code."""
    parent = module.removesuffix(".__init__") if module.endswith(".__init__") else module.rpartition(".")[0]
    edges: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            destination = node.module or ""
            if node.level:
                destination = importlib.util.resolve_name("." * node.level + destination, parent)
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


def run(
    repo: Path, profile: dict, argv: list[str], *, inventory: list[str] | None = None,
) -> list[Finding]:
    direction, tiers = import_config(profile)
    package, root = direction.get("package"), direction.get("source_root")
    if not (isinstance(package, str) and isinstance(root, str) and tiers):
        return []
    try:
        paths = repo_paths(repo) if inventory is None else inventory
    except InventoryError as error:
        return [Finding(DIRECTION, ".", str(error), "run inside a Git work tree")]
    scan = as_list(direction.get("scan")) or [root]
    modules: dict[str, str] = {}
    edges: dict[str, list[str]] = {}
    found: list[Finding] = []
    for path in paths:
        if not path.endswith(".py") or not matches_any(path, scan) or not (repo / path).is_file():
            continue
        relative = PurePosixPath(path).relative_to(root.rstrip("/")) if matches(path, root) else None
        module = ".".join([package, *relative.with_suffix("").parts]) if relative else package
        try:
            edges[path] = imported_edges(ast.parse((repo / path).read_bytes(), filename=path), module, package)
        except (OSError, SyntaxError, ValueError, ImportError) as error:
            found.append(Finding(DIRECTION, path, str(error), "restore readable Python imports"))
        if relative:
            modules[module.removesuffix(".__init__")] = path
    readers: dict[str, set[tuple[str, str]]] = {module: set() for module in modules}
    tier_readers: dict[str, set[tuple[str, str]]] = {tier: set() for tier in tiers[:-1]}
    for module, path in sorted(modules.items()):
        source = part(module, package, tiers)
        if source is None:
            continue
        for edge in sorted(set(edges.get(path, []))):
            destination = edge
            while destination not in modules and "." in destination:
                destination = destination.rpartition(".")[0]
            target = part(destination, package, tiers)
            if target is None or target == source:
                continue
            if source[0] == target[0]:
                found.append(Finding(SIBLING, path, f"sibling import {edge}", "move shared code to the lowest tier both siblings may read"))
            if tiers.index(source[0]) > tiers.index(target[0]):
                readers[destination].add(source)
                tier_readers[target[0]].add(source)
    for tier, users in tier_readers.items():
        populated = any(
            owner is not None and owner[0] == tier
            for module in modules
            for owner in [part(module, package, tiers)]
        )
        if populated and len(users) < 2:
            found.append(Finding(TIER, f"{root.rstrip('/')}/{tier}", f"tier is read by {len(users)} other parts", "keep modules flat until at least two parts read the tier"))
    for module, users in sorted(readers.items()):
        owner = part(module, package, tiers)
        if owner and owner[0] in tier_readers and len(users) == 1:
            tier, name = next(iter(users))
            found.append(Finding(OWNER, modules[module], f"shared module is read only by {tier}.{name}", f"move this module under {root.rstrip('/')}/{tier}/{name}"))
    return apply_exceptions(found, profile)
