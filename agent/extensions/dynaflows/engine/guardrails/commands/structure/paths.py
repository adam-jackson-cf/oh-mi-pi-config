"""GATE.path-check: placement, ownership, naming, test placement and import direction per path.

``guardrails path-check [path ...]`` checks the named paths, or the whole inventory. It reads:

- ``structure.owners``: ``{"files": {"<path>": "<owner>"}, "directories": {"<dir>/": "<owner>"},
  "patterns": {"<glob>": "<owner>"}}``. A directory owner covers files directly inside it.
- ``structure.tiers``: tier package names, lowest first; ``structure.import_direction``:
  ``{"package": "<import name>", "source_root": "<dir>", "scan": ["<dir>", ...],
  "native": {"command": [...], "cwd": "<dir>", "timeout_s": N}}``. A file may import its own
  tier or a lower one; ``native`` runs the repo's import-contract engine (e.g. import-linter).
- ``tiers.*.location``, ``layout`` and ``ownership_areas.map`` for test placement.
- ``guardrails.paths``: ``skip`` (patterns left unchecked) and ``directory_extensions``
  (suffixes a directory owner covers; default every file).

Layout allowlists come from ``guardrails.structure`` (see ``layout``) and names from ``naming``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure import layout, naming
from guardrails.commands.structure.import_direction import (
    direction_findings,
    native_findings,
)
from guardrails.commands.structure.import_graph import run as import_graph_findings
from guardrails.commands.structure.python_paths import python_path_findings
from guardrails.commands.structure.suite_placement import (
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
)

PLACEMENT = "STRUCT.folder-placement"


def ownership_findings(path: str, profile: dict[str, Any]) -> list[Finding]:
    owners = section(section(profile, "structure"), "owners")
    if path in owners.get("files", {}):
        return []
    extensions = as_list(settings(profile, "paths").get("directory_extensions"))
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
    profile: dict[str, Any],
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
    if matches_any(path, as_list(settings(profile, "paths").get("skip"))):
        return []
    problems = layout.path_findings(
        path, settings(profile, "structure"), existing_paths=existing_paths
    )
    python_findings = python_path_findings(path, profile, existing_paths or set())
    return [
        *problems,
        *([] if problems else ownership_findings(path, profile)),
        *naming.path_findings(repo, path, profile, existing_paths=existing_paths),
        *python_findings,
        *test_placement_findings(repo, path, profile),
        *direction_findings(repo, path, profile),
    ]


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        selected = scope.select(repo, argv)
    except InventoryError as error:
        return [Finding(PLACEMENT, ".", str(error), "run inside a Git work tree")]
    if not selected.paths:
        return []
    try:
        with scope.checkout(repo, selected) as root:
            found = [
                finding
                for raw in selected.paths
                for finding in path_findings(
                    root, raw, profile, existing_paths=selected.existing_paths
                )
            ]
            found.extend(native_findings(root, selected.paths, profile))
            found.extend(
                scope.restrict(
                    layout.instance_findings(
                        selected.inventory, settings(profile, "structure")
                    ),
                    selected.paths,
                    selected.existing_paths,
                )
            )
            found.extend(
                layout.forbidden_import_findings(
                    root,
                    selected.paths,
                    settings(profile, "structure"),
                )
            )
            found.extend(
                scope.restrict(
                    import_graph_findings(
                        root, profile, [], inventory=selected.inventory
                    ),
                    selected.paths,
                    selected.existing_paths,
                )
            )
    except InventoryError as error:
        return [Finding(PLACEMENT, ".", str(error), "provide a readable Git snapshot")]
    return apply_exceptions(found, profile)
