from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.context.config.adapter_declarations import tier_locations
from guardrails.context.config.reader import config_section, structure_settings
from guardrails.context.config.suite_files import is_test_file
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import matches_any
from guardrails.foundation.source import python, tree

if TYPE_CHECKING:
    from ast_grep_py import SgNode

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


def framework_names(
    found: list[python.PythonImport],
) -> tuple[bool, set[str], set[str]]:
    frameworks = conventions.test_frameworks("python")
    modules = set(frameworks["modules"])
    case_module, case_classes = frameworks["case_module"], frameworks["case_classes"]
    framework_imported = False
    testcase_names = set(case_classes)
    unittest_names = {case_module}
    for item in found:
        if item.level:
            continue
        framework_imported |= item.module.split(".")[0] in modules
        if item.statement == "import" and item.module == case_module:
            unittest_names.update(item.bindings)
        elif item.statement == "from" and item.module == case_module:
            testcase_names.update(
                bound
                for name, bound in zip(item.names, item.bindings, strict=True)
                if name in case_classes
            )
    return framework_imported, testcase_names, unittest_names


def is_test_class(
    node: SgNode, testcase_names: set[str], unittest_names: set[str]
) -> bool:
    frameworks = conventions.test_frameworks("python")
    bases = node.field("superclasses")
    return python.name_of(node).startswith(frameworks["class_prefix"]) or any(
        (base.kind() == "identifier" and base.text() in testcase_names)
        or (
            base.kind() == "attribute"
            and (attribute := base.field("attribute")) is not None
            and attribute.text() in frameworks["case_classes"]
            and (value := base.field("object")) is not None
            and value.kind() == "identifier"
            and value.text() in unittest_names
        )
        for base in ([] if bases is None else tree.named(bases))
    )


def defines_tests(repo: Path, path: str) -> bool:
    """Whether a Python file that runs (a stub defines no tests) imports a test
    framework and defines a test function or a test class with a test method, by the
    framework names the Python pack holds."""
    if (
        not path.endswith(conventions.runtime_extensions("python"))
        or not (repo / path).is_file()
    ):
        return False
    prefix = conventions.test_frameworks("python")["function_prefix"]
    try:
        root = tree.parse(path, tree.read(repo, path))
    except (OSError, tree.SourceError):
        return False
    framework_imported, testcase_names, unittest_names = framework_names(
        python.imports(root)
    )
    if not framework_imported:
        return False
    for node in python.members(root):
        if node.kind() == "function_definition" and python.name_of(node).startswith(
            prefix
        ):
            return True
        if (
            node.kind() == "class_definition"
            and is_test_class(node, testcase_names, unittest_names)
            and any(
                method.kind() == "function_definition"
                and python.name_of(method).startswith(prefix)
                for method in python.members(node.field("body"))
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
