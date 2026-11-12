"""GATE.path-check: placement, ownership, naming, test placement and import direction per path.

``omp-guardrails path-check [path ...]`` checks the named paths, or the whole inventory. It reads:

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

import ast
import importlib.util
import subprocess
from pathlib import Path, PurePosixPath

from omp_guardrails.child_env import repo_environment
from omp_guardrails.config.house_style import apply_exceptions
from omp_guardrails.findings import Finding
from omp_guardrails.structure import layout, naming
from omp_guardrails.structure.inventory import (
    InventoryError,
    as_list,
    matches,
    matches_any,
    normalized_path,
    selected_paths,
    settings,
)

PLACEMENT = "STRUCT.folder-placement"
DIRECTION = "STRUCT.dependency-direction"
TEST_PLACEMENT = "TEST.placement"
TIER_KEYS = ("t1", "t2", "t3")


def section(profile: dict, key: str) -> dict:
    value = profile.get(key)
    return value if isinstance(value, dict) else {}


def ownership_findings(path: str, profile: dict) -> list[Finding]:
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


def tier_locations(profile: dict) -> dict[str, list[str]]:
    tiers = section(profile, "tiers")
    return {
        key: as_list(tiers[key].get("location"))
        for key in TIER_KEYS
        if isinstance(tiers.get(key), dict)
    }


def defines_tests(repo: Path, path: str) -> bool:
    if not path.endswith(".py") or not (repo / path).is_file():
        return False
    try:
        tree = ast.parse((repo / path).read_bytes(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        for node in ast.walk(tree)
    )


def test_placement_findings(repo: Path, path: str, profile: dict) -> list[Finding]:
    """Runnable tests sit under a tier location, outside fixtures and support, and T2 files
    map to an ownership area. A file whose location several tiers share is left to the
    adapter's tier collection (TEST.tier-selectors)."""
    layout_section = section(profile, "layout")
    holders = [
        d
        for d in (layout_section.get("fixtures_dir"), layout_section.get("support_dir"))
        if isinstance(d, str)
    ]
    in_holder = matches_any(path, holders)
    is_test = naming.is_test_file(path, profile)
    if not is_test:
        if (
            not in_holder
            and defines_tests(repo, path)
            and not matches_any(
                path, [loc for locs in tier_locations(profile).values() for loc in locs]
            )
        ):
            return [
                Finding(
                    TEST_PLACEMENT,
                    path,
                    "test functions live outside every tier location",
                    "move them into a test file at a tier location",
                )
            ]
        return []
    if in_holder:
        return [
            Finding(
                TEST_PLACEMENT,
                path,
                "runnable test inside a fixtures or support directory",
                "move it to its tier location or rename it so the runner does not collect it",
            )
        ]
    tiers = [
        key for key, locations in tier_locations(profile).items() if matches_any(path, locations)
    ]
    if not tiers:
        return [
            Finding(
                TEST_PLACEMENT,
                path,
                "test file is outside every tier location",
                "move it under its tier's location from the profile",
            )
        ]
    areas = section(section(profile, "ownership_areas"), "map")
    if tiers == ["t2"] and not matches_any(path, list(areas)):
        return [
            Finding(
                TEST_PLACEMENT,
                path,
                "T2 test file maps to no ownership area",
                "add its pattern to ownership_areas.map",
            )
        ]
    return []


def import_config(profile: dict) -> tuple[dict, list[str]]:
    structure = section(profile, "structure")
    direction = structure.get("import_direction")
    return (direction if isinstance(direction, dict) else {}), as_list(structure.get("tiers"))


def imported_edges(tree: ast.Module, module: str, package: str) -> list[str]:
    """Import destinations inside ``package``, resolved without importing authored code."""
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
                destination = importlib.util.resolve_name("." * node.level + destination, parent)
            if destination == package or not node.module:
                edges.extend(f"{destination}.{alias.name}" for alias in node.names)
            elif destination:
                edges.append(destination)
    return [edge for edge in edges if edge == package or edge.startswith(f"{package}.")]


def direction_findings(repo: Path, path: str, profile: dict) -> list[Finding]:
    direction, tiers = import_config(profile)
    package, source_root = direction.get("package"), direction.get("source_root")
    if not (isinstance(package, str) and isinstance(source_root, str) and path.endswith(".py")):
        return []
    if not matches_any(path, as_list(direction.get("scan")) or [source_root]):
        return []
    source = repo / path
    if not source.is_file():
        return []
    inside = matches(path, source_root)
    relative = PurePosixPath(path).relative_to(source_root.rstrip("/")) if inside else None
    module = ".".join([package, *relative.with_suffix("").parts]) if relative else package
    try:
        tree = ast.parse(source.read_bytes(), filename=path)
        edges = imported_edges(tree, module, package)
    except (OSError, SyntaxError, ValueError, ImportError) as error:
        return [Finding(DIRECTION, path, str(error), "restore readable Python imports")]
    owner = relative.parts[0] if relative and len(relative.parts) > 1 else None
    found = []
    for edge in edges:
        destination = edge.split(".")[1] if "." in edge else None
        allowed = (
            owner in tiers
            and destination in tiers
            and tiers.index(destination) <= tiers.index(owner)
        )
        if not allowed:
            found.append(
                Finding(
                    DIRECTION,
                    path,
                    f"unmapped or upward import {edge}",
                    "import only your own or a lower tier from structure.tiers",
                )
            )
    return found


def native_findings(repo: Path, sources: list[str], profile: dict) -> list[Finding]:
    native = import_config(profile)[0].get("native")
    if not sources or not isinstance(native, dict):
        return []
    command, timeout = as_list(native.get("command")), native.get("timeout_s")
    if not command or not isinstance(timeout, (int, float)):
        problem = "structure.import_direction.native needs command and timeout_s"
    else:
        try:
            result = subprocess.run(
                command,
                cwd=repo / native.get("cwd", "."),
                env=repo_environment(),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
            if result.returncode == 0:
                return []
            problem = "repository import contracts failed"
        except (OSError, subprocess.TimeoutExpired) as error:
            problem = f"cannot run the import-contract engine: {error}"
    return [
        Finding(DIRECTION, path, problem, f"run {' '.join(command)} and remove forbidden edges")
        for path in sources
    ]


def path_findings(repo: Path, raw: str, profile: dict) -> list[Finding]:
    path = normalized_path(repo, raw)
    if path is None:
        return [
            Finding(
                PLACEMENT, raw, "unsafe repository path", "use a repository path without traversal"
            )
        ]
    if matches_any(path, as_list(settings(profile, "paths").get("skip"))):
        return []
    problems = layout.path_findings(path, settings(profile, "structure"))
    return [
        *problems,
        *([] if problems else ownership_findings(path, profile)),
        *naming.path_findings(repo, path, profile),
        *test_placement_findings(repo, path, profile),
        *direction_findings(repo, path, profile),
    ]


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    try:
        paths = selected_paths(repo, argv)
    except InventoryError as error:
        return [Finding(PLACEMENT, ".", str(error), "run inside a Git work tree")]
    found = [finding for raw in paths for finding in path_findings(repo, raw, profile)]
    direction, _ = import_config(profile)
    scan = as_list(direction.get("scan")) or as_list(direction.get("source_root"))
    sources = sorted({
        p
        for raw in paths
        if (p := normalized_path(repo, raw)) and p.endswith(".py") and matches_any(p, scan)
    })
    found.extend(native_findings(repo, sources, profile))
    return apply_exceptions(found, profile)
