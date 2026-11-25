from __future__ import annotations

import ast
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming import package_placement
from guardrails.commands.structure_naming.import_direction import native_findings
from guardrails.commands.structure_naming.import_graph import import_graph_findings
from guardrails.context import scope
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding, config_label
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches,
    matches_any,
    settings,
)

PLACEMENT = "STRUCT.folder-placement"
DIRECTION = "STRUCT.dependency-direction"
SETTINGS_FIX = (
    "or add the entry to checkers.layout in .agentrails/config.json with owner approval"
)


def placement(path: str, message: str, fix: str = "") -> Finding:
    return Finding(
        PLACEMENT,
        path,
        message,
        fix or f"move it to an allowlisted location {SETTINGS_FIX}",
    )


def root_findings(path: str, options: dict[str, Any]) -> list[Finding]:
    parts = PurePosixPath(path).parts
    if len(parts) == 1:
        allowed = options.get("root_files")
        if isinstance(allowed, list) and parts[0] not in allowed:
            return [placement(parts[0], f"unexpected root file {parts[0]}")]
        return []
    allowed = options.get("root_directories")
    if isinstance(allowed, list) and parts[0] not in allowed:
        return [
            placement(f"{parts[0]}/", f"unexpected top-level directory {parts[0]}/")
        ]
    return []


def area_findings(path: str, options: dict[str, Any]) -> list[Finding]:
    found = []
    areas = options.get("areas", {})
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
            found.append(
                placement(f"{area}/{child}", f"unexpected {kind} {child} in {area}/")
            )
    return found


def package_findings(path: str, options: dict[str, Any]) -> list[Finding]:
    found = []
    for package in options.get("packages", []):
        root = package["root"].rstrip("/")
        if not path.startswith(f"{root}/"):
            continue
        parts = PurePosixPath(path[len(root) + 1 :]).parts
        children = package.get("children", {})
        if len(parts) > 1 and parts[0] not in children:
            found.append(
                placement(
                    f"{root}/{parts[0]}/", f"unexpected package {parts[0]}/ in {root}/"
                )
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


def instance_findings(paths: list[str], options: dict[str, Any]) -> list[Finding]:
    found = []
    for kind in options.get("instances", []):
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


def forbidden_import_findings(
    repo: Path, paths: list[str], options: dict[str, Any]
) -> list[Finding]:
    found = []
    for rule in options.get("forbidden_imports", []):
        found.extend(rule_import_findings(repo, paths, rule))
    return found


def rule_import_findings(
    repo: Path, paths: list[str], rule: dict[str, Any]
) -> list[Finding]:
    banned = set(as_list(rule.get("modules")))
    found = []
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
            "PYS000",
            path,
            f"cannot parse Python source: {error}",
            "restore readable Python",
        )


def imported_modules(tree: ast.Module) -> list[tuple[int, str]]:
    names: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append((node.lineno, node.module))
    return names


def path_findings(
    path: str,
    options: dict[str, Any],
    *,
    existing_paths: set[str] | None = None,
) -> list[Finding]:
    return scope.restrict(
        [
            *root_findings(path, options),
            *area_findings(path, options),
            *package_findings(path, options),
        ],
        [path],
        existing_paths,
    )


def package_placement_findings(
    paths: list[str], inventory: list[str], config: dict[str, Any]
) -> list[Finding]:
    return package_placement.package_file_findings(paths, inventory, config)


def configuration_findings(options: dict[str, Any]) -> list[Finding]:
    if isinstance(options.get("root_directories"), list) and isinstance(
        options.get("root_files"), list
    ):
        return []
    return [
        placement(
            config_label("checkers"),
            "checkers.layout.root_directories and root_files are not configured",
            "record the approved root allowlist in checkers.layout",
        )
    ]


def skipped(path: str, options: dict[str, Any]) -> bool:
    return matches_any(path, as_list(options.get("skip")))


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = settings(config, "layout")
    try:
        selected = scope.select(repo, argv)
        paths = [path for path in selected.paths if not skipped(path, options)]
    except InventoryError as error:
        return [placement(".", str(error), "run inside a Git work tree")]
    if not paths and selected.existing_paths is not None:
        return []
    found = configuration_findings(options)
    try:
        with scope.checkout(repo, selected) as root:
            found.extend(
                scope.restrict(
                    import_graph_findings(root, config, inventory=selected.inventory),
                    paths,
                    selected.existing_paths,
                )
            )
            for path in paths:
                found.extend(
                    path_findings(path, options, existing_paths=selected.existing_paths)
                )
            found.extend(
                scope.restrict(
                    instance_findings(selected.inventory, options),
                    paths,
                    selected.existing_paths,
                )
            )
            found.extend(forbidden_import_findings(root, paths, options))
            found.extend(package_placement_findings(paths, selected.inventory, config))
            found.extend(native_findings(root, paths, config))
    except InventoryError as error:
        return [placement(".", str(error), "provide a readable Git snapshot")]
    return apply_exceptions(
        sorted(set(found), key=lambda item: (item.path, item.message)), config
    )
