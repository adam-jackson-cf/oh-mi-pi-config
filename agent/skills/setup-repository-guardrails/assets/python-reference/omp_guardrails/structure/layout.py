"""STRUCT.folder-placement and STRUCT.dependency-direction from the repository layout.

``omp-guardrails structure`` checks the whole inventory against the profile's
``guardrails.structure`` settings (all optional except the root allowlist):

- ``root_directories`` / ``root_files``: the only entries allowed at the repository root.
- ``areas``: ``{"<folder>": {"directories": [...], "files": [...]}}`` allowlists the direct
  children of a data folder.
- ``packages``: ``[{"root": "<dir>", "children": {"<pkg>": ["<subpkg>", ...]}, "max_depth": N}]``
  allowlists the packages below a source root, each with its own subpackages, and bounds nesting.
- ``instances``: ``[{"root": "<dir>", "depth": N, "marker": "<file>", "exempt": [...]}]``: every
  file under ``root`` sits inside an instance folder ``N`` levels below it, and each instance
  folder holds ``marker`` directly (one layout for every instance of a kind).
- ``forbidden_imports``: ``[{"root": "<dir>", "modules": [...]}]``: Python files under ``root``
  never import those top-level modules.
"""

from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath

from omp_guardrails.config.house_style import apply_exceptions
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import (
    InventoryError,
    as_list,
    matches,
    matches_any,
    selected_paths,
    settings,
)

PLACEMENT = "STRUCT.folder-placement"
DIRECTION = "STRUCT.dependency-direction"
SETTINGS_FIX = (
    "or add the entry to guardrails.structure in .omp/test-profile.md with owner approval"
)


def placement(path: str, message: str, fix: str = "") -> Finding:
    return Finding(
        PLACEMENT, path, message, fix or f"move it to an allowlisted location {SETTINGS_FIX}"
    )


def root_findings(path: str, config: dict) -> list[Finding]:
    parts = PurePosixPath(path).parts
    if len(parts) == 1:
        allowed = config.get("root_files")
        if isinstance(allowed, list) and parts[0] not in allowed:
            return [placement(parts[0], f"unexpected root file {parts[0]}")]
        return []
    allowed = config.get("root_directories")
    if isinstance(allowed, list) and parts[0] not in allowed:
        return [placement(f"{parts[0]}/", f"unexpected top-level directory {parts[0]}/")]
    return []


def area_findings(path: str, config: dict) -> list[Finding]:
    found = []
    areas = config.get("areas", {})
    for area, allowed in areas.items() if isinstance(areas, dict) else []:
        prefix = PurePosixPath(area).parts
        parts = PurePosixPath(path).parts
        if parts[: len(prefix)] != prefix or len(parts) <= len(prefix):
            continue
        child = parts[len(prefix)]
        direct_file = len(parts) == len(prefix) + 1
        names = as_list(allowed.get("files" if direct_file else "directories"))
        if child not in names:
            kind = "file" if direct_file else "directory"
            found.append(placement(f"{area}/{child}", f"unexpected {kind} {child} in {area}/"))
    return found


def package_findings(path: str, config: dict) -> list[Finding]:
    found = []
    for package in config.get("packages", []):
        root = package["root"].rstrip("/")
        if not path.startswith(f"{root}/"):
            continue
        parts = PurePosixPath(path[len(root) + 1 :]).parts
        children = package.get("children", {})
        if len(parts) > 1 and parts[0] not in children:
            found.append(
                placement(f"{root}/{parts[0]}/", f"unexpected package {parts[0]}/ in {root}/")
            )
        elif len(parts) > 2 and parts[1] not in children[parts[0]]:
            found.append(
                placement(
                    f"{root}/{parts[0]}/{parts[1]}/",
                    f"unexpected {parts[0]} subpackage {parts[1]}/",
                )
            )
        max_depth = package.get("max_depth")
        if isinstance(max_depth, int) and len(parts) - 1 > max_depth:
            found.append(
                placement(
                    str(PurePosixPath(path).parent),
                    f"package nests deeper than {max_depth} below {root}/",
                    "flatten the package or move run-time data out of the source tree",
                )
            )
    return found


def instance_findings(paths: list[str], config: dict) -> list[Finding]:
    found = []
    for kind in config.get("instances", []):
        root = PurePosixPath(kind["root"])
        depth = int(kind["depth"])
        marker = kind["marker"]
        exempt = as_list(kind.get("exempt"))
        folders: set[PurePosixPath] = set()
        marked: set[PurePosixPath] = set()
        for path in paths:
            pure = PurePosixPath(path)
            if not pure.is_relative_to(root) or pure == root or path in exempt:
                continue
            below = pure.relative_to(root).parts
            if len(below) <= depth:
                found.append(
                    placement(
                        path,
                        f"file sits outside an instance folder {depth} levels below {root}/",
                    )
                )
                continue
            folder = root.joinpath(*below[:depth])
            folders.add(folder)
            if len(below) == depth + 1 and below[-1] == marker:
                marked.add(folder)
        found.extend(
            placement(
                f"{folder}/",
                f"instance folder has no {marker}",
                f"add {marker} or remove the folder",
            )
            for folder in sorted(folders - marked)
        )
    return found


def forbidden_import_findings(repo: Path, paths: list[str], config: dict) -> list[Finding]:
    found = []
    for rule in config.get("forbidden_imports", []):
        banned = set(as_list(rule.get("modules")))
        for path in paths:
            if not (path.endswith(".py") and matches(path, rule["root"])):
                continue
            tree = parse_python(repo, path)
            if isinstance(tree, Finding):
                found.append(tree)
                continue
            found.extend(
                Finding(
                    DIRECTION,
                    path,
                    f"line {line}: files under {rule['root']} never import {name}",
                    "move the dependency behind a lower-tier contract",
                )
                for line, name in imported_modules(tree)
                if name.split(".")[0] in banned
            )
    return found


def parse_python(repo: Path, path: str) -> ast.Module | Finding:
    try:
        return ast.parse((repo / path).read_bytes(), filename=path)
    except (OSError, SyntaxError, ValueError) as error:
        return Finding(
            "PYS000", path, f"cannot parse Python source: {error}", "restore readable Python"
        )


def imported_modules(tree: ast.Module) -> list[tuple[int, str]]:
    """Absolute imports as (line, module) pairs."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append((node.lineno, node.module))
    return names


def path_findings(path: str, config: dict) -> list[Finding]:
    """Per-path allowlist findings, shared with the changed-path check."""
    return [
        *root_findings(path, config),
        *area_findings(path, config),
        *package_findings(path, config),
    ]


def configuration_findings(config: dict) -> list[Finding]:
    if isinstance(config.get("root_directories"), list) and isinstance(
        config.get("root_files"), list
    ):
        return []
    return [
        placement(
            ".omp/test-profile.md",
            "guardrails.structure.root_directories and root_files are not configured",
            "record the approved root allowlist in the profile",
        )
    ]


def skipped(path: str, config: dict) -> bool:
    return matches_any(path, as_list(config.get("skip")))


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    config = settings(profile, "structure")
    try:
        paths = [path for path in selected_paths(repo, argv) if not skipped(path, config)]
    except InventoryError as error:
        return [placement(".", str(error), "run inside a Git work tree")]
    found = configuration_findings(config)
    for path in paths:
        found.extend(path_findings(path, config))
    found.extend(instance_findings(paths, config))
    found.extend(forbidden_import_findings(repo, paths, config))
    return apply_exceptions(sorted(set(found), key=lambda item: (item.path, item.message)), profile)
