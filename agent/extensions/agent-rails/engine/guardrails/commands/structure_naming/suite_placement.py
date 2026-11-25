from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from guardrails.context.config.adapter_declarations import tier_locations
from guardrails.context.config.reader import config_section, structure_settings
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import matches_any

UNPLACED_FUNCTIONS = (
    "test functions live outside every tier location",
    "move them into a test file at a tier location",
)


HELD_TEST = (
    "runnable test inside a fixtures or support directory",
    "move it to its tier location or rename it so the runner does not collect it",
)


def section(values: dict[str, Any], key: str) -> dict[str, Any]:
    value = values.get(key)
    return value if isinstance(value, dict) else {}


UNTIERED_TEST = (
    "test file is outside every tier location",
    "move it under its tier's location from tests.adapters",
)


UNOWNED_T2 = (
    "T2 test file maps to no ownership area",
    "add its pattern to ownership_areas.map",
)

TEST_PLACEMENT = "TEST.placement"


def framework_names(tree: ast.Module) -> tuple[bool, set[str], set[str]]:
    frameworks = conventions.load_pack("python")["test_frameworks"]
    modules = set(frameworks["modules"])
    case_module, case_class = frameworks["case_module"], frameworks["case_class"]
    framework_imported = False
    testcase_names = {case_class}
    unittest_names = {case_module}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                framework_imported |= alias.name.split(".")[0] in modules
                if alias.name == case_module:
                    unittest_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            framework_imported |= (node.module or "").split(".")[0] in modules
            if node.module == case_module:
                testcase_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == case_class
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


def placement_problem(
    repo: Path, path: str, config: dict[str, Any]
) -> tuple[str, str] | None:
    layout_section = {
        **conventions.placement(path),
        **section(config_section(config, "tests"), "layout"),
    }
    holders = [
        d
        for d in (layout_section.get("fixtures_dir"), layout_section.get("support_dir"))
        if isinstance(d, str)
    ]
    in_holder = matches_any(path, holders)
    if (
        not tier_locations(config)
        and layout_section.get("beside_code")
        and not in_holder
    ):
        return None
    if not is_test_file(path, config):
        unplaced = (
            not in_holder
            and defines_tests(repo, path)
            and not matches_any(
                path, [loc for locs in tier_locations(config).values() for loc in locs]
            )
        )
        return UNPLACED_FUNCTIONS if unplaced else None
    if in_holder:
        return HELD_TEST
    tiers = [
        key
        for key, locations in tier_locations(config).items()
        if matches_any(path, locations)
    ]
    if not tiers:
        return UNTIERED_TEST
    areas = section(section(structure_settings(config), "ownership_areas"), "map")
    return (
        UNOWNED_T2 if tiers == ["t2"] and not matches_any(path, list(areas)) else None
    )


def test_placement_findings(
    repo: Path, path: str, config: dict[str, Any]
) -> list[Finding]:
    problem = placement_problem(repo, path, config)
    if problem is None:
        return []
    message, fix = problem
    return [Finding(TEST_PLACEMENT, path, message, fix)]
