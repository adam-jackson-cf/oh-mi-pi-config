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

import ast
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure import layout, naming
from guardrails.commands.structure.import_direction import (
    direction_findings,
    native_findings,
)
from guardrails.commands.structure.import_graph import run as import_graph_findings
from guardrails.context import scope
from guardrails.context.config import adapter_declarations
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches_any,
    normalized_path,
    settings,
)
from guardrails.toolkit.python_rules import path_names, scope as python_scope

PLACEMENT = "STRUCT.folder-placement"
TEST_PLACEMENT = "TEST.placement"


def section(profile: dict[str, Any], key: str) -> dict[str, Any]:
    value = profile.get(key)
    return value if isinstance(value, dict) else {}


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


def tier_locations(profile: dict[str, Any]) -> dict[str, list[str]]:
    """Tier -> the test locations every adapter taking part in it declares."""
    return adapter_declarations.tier_locations(profile)


def framework_names(tree: ast.Module) -> tuple[bool, set[str], set[str]]:
    """Whether pytest or unittest is imported, and the local names of ``TestCase`` and ``unittest``."""
    framework_imported = False
    testcase_names = {"TestCase"}
    unittest_names = {"unittest"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                framework_imported |= alias.name.split(".")[0] in {"pytest", "unittest"}
                if alias.name == "unittest":
                    unittest_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            framework_imported |= (node.module or "").split(".")[0] in {
                "pytest",
                "unittest",
            }
            if node.module == "unittest":
                testcase_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "TestCase"
                )
    return framework_imported, testcase_names, unittest_names


def is_test_class(
    node: ast.ClassDef, testcase_names: set[str], unittest_names: set[str]
) -> bool:
    return node.name.startswith("Test") or any(
        (isinstance(base, ast.Name) and base.id in testcase_names)
        or (
            isinstance(base, ast.Attribute)
            and base.attr == "TestCase"
            and isinstance(base.value, ast.Name)
            and base.value.id in unittest_names
        )
        for base in node.bases
    )


def defines_tests(repo: Path, path: str) -> bool:
    """Recognize framework-backed module tests and direct test-class methods, not helpers."""
    if not path.endswith(".py") or not (repo / path).is_file():
        return False
    try:
        tree = ast.parse((repo / path).read_bytes(), filename=path)
    except (OSError, SyntaxError, ValueError):
        return False
    framework_imported, testcase_names, unittest_names = framework_names(tree)
    if not framework_imported:
        return False
    function_types = (ast.FunctionDef, ast.AsyncFunctionDef)
    for node in tree.body:
        if isinstance(node, function_types) and node.name.startswith("test_"):
            return True
        if (
            isinstance(node, ast.ClassDef)
            and is_test_class(node, testcase_names, unittest_names)
            and any(
                isinstance(method, function_types) and method.name.startswith("test_")
                for method in node.body
            )
        ):
            return True
    return False


UNPLACED_FUNCTIONS = (
    "test functions live outside every tier location",
    "move them into a test file at a tier location",
)
HELD_TEST = (
    "runnable test inside a fixtures or support directory",
    "move it to its tier location or rename it so the runner does not collect it",
)
UNTIERED_TEST = (
    "test file is outside every tier location",
    "move it under its tier's location from the profile",
)
UNOWNED_T2 = (
    "T2 test file maps to no ownership area",
    "add its pattern to ownership_areas.map",
)


def placement_problem(
    repo: Path, path: str, profile: dict[str, Any]
) -> tuple[str, str] | None:
    """``(message, fix)`` for a misplaced test file or test-defining module, else None."""
    layout_section = {**conventions.placement(path), **section(profile, "layout")}
    holders = [
        d
        for d in (layout_section.get("fixtures_dir"), layout_section.get("support_dir"))
        if isinstance(d, str)
    ]
    in_holder = matches_any(path, holders)
    if (
        not tier_locations(profile)
        and layout_section.get("beside_code")
        and not in_holder
    ):
        return None
    if not is_test_file(path, profile):
        unplaced = (
            not in_holder
            and defines_tests(repo, path)
            and not matches_any(
                path, [loc for locs in tier_locations(profile).values() for loc in locs]
            )
        )
        return UNPLACED_FUNCTIONS if unplaced else None
    if in_holder:
        return HELD_TEST
    tiers = [
        key
        for key, locations in tier_locations(profile).items()
        if matches_any(path, locations)
    ]
    if not tiers:
        return UNTIERED_TEST
    areas = section(section(profile, "ownership_areas"), "map")
    return (
        UNOWNED_T2 if tiers == ["t2"] and not matches_any(path, list(areas)) else None
    )


def test_placement_findings(
    repo: Path, path: str, profile: dict[str, Any]
) -> list[Finding]:
    """Runnable tests sit under a tier location, outside fixtures and support, and T2 files
    map to an ownership area. A file whose location several tiers share is left to the
    adapter's tier collection (TEST.tier-selectors)."""
    problem = placement_problem(repo, path, profile)
    if problem is None:
        return []
    message, fix = problem
    return [Finding(TEST_PLACEMENT, path, message, fix)]


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
    python_findings = []
    if "python" in section(profile, "guardrails"):
        config = python_scope.load_settings(profile)
        existing_segments = {
            "/".join(parts[:index])
            for item in existing_paths or ()
            for parts in (PurePosixPath(item).parts,)
            for index in range(1, len(parts) + 1)
        }
        if not python_scope.is_excluded(path, config):
            python_findings = (
                path_names.structure_findings(path, config, existing_segments)
                if path.endswith(".py")
                else path_names.skill_findings(path, config, existing_segments)
            )
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
