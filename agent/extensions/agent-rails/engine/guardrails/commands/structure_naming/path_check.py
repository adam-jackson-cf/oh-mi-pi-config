from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming import layout, naming
from guardrails.commands.structure_naming.import_direction import (
    direction_findings,
    native_findings,
)
from guardrails.commands.structure_naming.import_graph import import_graph_findings
from guardrails.commands.structure_naming.python_paths import python_path_findings
from guardrails.commands.structure_naming.suite_placement import (
    section,
    test_placement_findings,
)
from guardrails.context import scope
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches_any,
    normalized_path,
    settings,
    structure_settings,
)

PLACEMENT = "STRUCT.folder-placement"
GATE = "GATE.path-check"


def ownership_findings(path: str, config: dict[str, Any]) -> list[Finding]:
    owners = section(structure_settings(config), "owners")
    if path in owners.get("files", {}):
        return []
    extensions = as_list(settings(config, "paths").get("directory_extensions"))
    directory = f"{PurePosixPath(path).parent.as_posix()}/"
    if directory in owners.get("directories", {}) and (
        not extensions or any(path.endswith(ext) for ext in extensions)
    ):
        return []
    if matches_any(path, list(owners.get("patterns", {}))):
        return []
    return [
        Finding(
            PLACEMENT,
            path,
            "file has no declared capability owner",
            "move it to an existing capability or declare its owner in structure.owners",
        )
    ]


def path_findings(
    repo: Path,
    raw: str,
    config: dict[str, Any],
    *,
    existing_paths: set[str] | None = None,
) -> list[Finding]:
    path = normalized_path(repo, raw)
    if path is None:
        return [
            Finding(
                PLACEMENT,
                raw,
                "unsafe repository path",
                "use a repository path without traversal",
            )
        ]
    if matches_any(path, as_list(settings(config, "paths").get("skip"))):
        return []
    problems = layout.path_findings(
        path, settings(config, "layout"), existing_paths=existing_paths
    )
    python_findings = python_path_findings(path, config, existing_paths or set())
    return [
        *problems,
        *([] if problems else ownership_findings(path, config)),
        *naming.path_findings(repo, path, config, existing_paths=existing_paths),
        *python_findings,
        *test_placement_findings(repo, path, config),
        *direction_findings(repo, path, config),
    ]


def owned_paths(root: Path, raw_paths: list[str], config: dict[str, Any]) -> list[str]:
    skip = as_list(settings(config, "paths").get("skip"))
    found = (normalized_path(root, raw) for raw in raw_paths)
    return [path for path in found if path is not None and not matches_any(path, skip)]


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        selected = scope.select(repo, argv)
    except InventoryError as error:
        return [Finding(GATE, ".", str(error), "run inside a Git work tree")]
    if not selected.paths:
        return []
    try:
        with scope.checkout(repo, selected) as root:
            found = [
                finding
                for raw in selected.paths
                for finding in path_findings(
                    root, raw, config, existing_paths=selected.existing_paths
                )
            ]
            found.extend(native_findings(root, selected.paths, config))
            found.extend(
                layout.package_placement_findings(
                    owned_paths(root, selected.paths, config),
                    selected.inventory,
                    config,
                )
            )
            found.extend(
                scope.restrict(
                    layout.instance_findings(
                        selected.inventory, settings(config, "layout")
                    ),
                    selected.paths,
                    selected.existing_paths,
                )
            )
            found.extend(
                layout.forbidden_import_findings(
                    root,
                    selected.paths,
                    settings(config, "layout"),
                )
            )
            found.extend(
                scope.restrict(
                    import_graph_findings(root, config, inventory=selected.inventory),
                    selected.paths,
                    selected.existing_paths,
                )
            )
    except InventoryError as error:
        return [Finding(GATE, ".", str(error), "provide a readable Git snapshot")]
    return apply_exceptions(found, config)
