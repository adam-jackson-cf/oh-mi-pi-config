from __future__ import annotations

import ast
import re
from collections.abc import Set
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.context import scope
from guardrails.context.config import house_style
from guardrails.context.config.suite_files import test_patterns
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches_any,
    name_matches,
    normalized_path,
    path_prefixes,
    settings,
)

CONVENTIONS = "NAME.conventions"
TEST_FILES = "NAME.test-files"


def casing_label(casings: list[str]) -> str:
    return " or ".join(casings)


def convention(path: str, message: str, fix: str) -> Finding:
    return Finding(CONVENTIONS, path, message, fix)


def word_findings(
    path: str, existing_segments: Set[str] = frozenset()
) -> list[Finding]:
    parts = PurePosixPath(path).parts
    found = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if "/".join(parts[: index + 1]) in existing_segments:
            continue
        if (last and conventions.fixed_file(path)) or (
            not last and conventions.fixed_folder(path, part)
        ):
            continue
        name = house_style.stem(part) if last else part
        if house_style.is_banned_word(name):
            kind = "module" if last else "directory"
            found.append(
                convention(
                    path,
                    f"generic catch-all {kind} name '{name}'",
                    "rename it for its owner or responsibility",
                )
            )
    return found


def version_findings(
    path: str, options: dict[str, Any], existing_segments: Set[str] = frozenset()
) -> list[Finding]:
    if matches_any(path, as_list(options.get("version_exempt"))):
        return []
    rules = house_style.load_house_style()["versioning"]
    standalone = options.get("standalone_versions", rules["standalone_allowed"])
    parts = PurePosixPath(path).parts
    for index, part in enumerate(parts):
        if "/".join(parts[: index + 1]) in existing_segments:
            continue
        problem = house_style.version_problem(
            part,
            last=index == len(parts) - 1,
            standalone_allowed=bool(standalone)
            and not (
                index == len(parts) - 1 and conventions.for_path(path)["extensions"]
            ),
        )
        if problem:
            return [
                convention(
                    path, problem, "put versions in data or a <name>/v<N> folder"
                )
            ]
    return []


def parent_findings(path: str, existing_paths: set[str] | None = None) -> list[Finding]:
    repeated = house_style.child_repeats_parent(
        PurePosixPath(path).parts, existing_paths=existing_paths
    )
    if repeated is None:
        return []
    return [
        Finding(
            CONVENTIONS,
            path,
            f"'{repeated}' repeats its parent folder's name",
            "name the child by its role",
            check="child-repeats-parent",
        )
    ]


def plural_findings(
    path: str, options: dict[str, Any], existing_segments: Set[str] = frozenset()
) -> list[Finding]:
    if not options.get("check_plurals"):
        return []
    allowed = set(as_list(options.get("plural_allowlist")))
    return [
        convention(
            path,
            f"plural directory name '{part}'",
            "use a singular noun unless it is a catalogue",
        )
        for index, part in enumerate(PurePosixPath(path).parts[:-1])
        if "/".join(PurePosixPath(path).parts[: index + 1]) not in existing_segments
        and not conventions.fixed_folder(path, part)
        and re.fullmatch(r"[a-z][a-z0-9_-]*[^s]s", part)
        and not house_style.is_catalogue_plural(part)
        and part not in allowed
    ]


def casing_findings(
    repo: Path, path: str, existing_segments: Set[str] = frozenset()
) -> list[Finding]:
    rules = conventions.for_path(path)
    name = rules["name"]
    marker = rules.get("package_marker")
    parts = PurePosixPath(path).parts
    found = []
    for index, segment in enumerate(parts):
        last = index == len(parts) - 1
        if "/".join(parts[: index + 1]) in existing_segments:
            continue
        if (last and conventions.fixed_file(path)) or (
            not last and conventions.fixed_folder(path, segment)
        ):
            continue
        if (
            not last
            and marker
            and not repo.joinpath(*parts[: index + 1], marker).is_file()
        ):
            continue
        casings = rules["file"] if last else rules["directory"]
        candidate = house_style.stem(segment) if last else segment
        if (
            casings
            and not segment.startswith(".")
            and not house_style.matches_casing(candidate, casings)
        ):
            kind = "file" if last else "directory"
            found.append(
                convention(
                    path,
                    f"{name} {kind} '{segment}' is not {casing_label(casings)}",
                    f"rename the {kind}",
                )
            )
    return found


def test_file_findings(path: str, config: dict[str, Any]) -> list[Finding]:
    patterns = test_patterns(config)
    test_like = conventions.test_like_patterns(path)
    if not any(name_matches(path, pattern) for pattern in patterns):
        if any(name_matches(path, pattern) for pattern in test_like):
            return [
                Finding(
                    TEST_FILES,
                    path,
                    "test-like file matches no configured test file pattern "
                    "(tests.layout.test_file_patterns), so the runner skips it",
                    f"rename it to match one of {patterns}",
                )
            ]
        return []
    return [
        Finding(
            TEST_FILES,
            path,
            f"test file name has {problem}",
            "name the file for the owner and behaviour it checks",
        )
        for problem in house_style.test_name_problems(PurePosixPath(path).name)
    ]


def symbol_findings(repo: Path, path: str, options: dict[str, Any]) -> list[Finding]:
    if conventions.for_path(path)["name"] != "python":
        return []
    source = repo / path
    if not source.is_file():
        return []
    try:
        tree = ast.parse(source.read_bytes(), filename=path)
    except (OSError, SyntaxError, ValueError) as error:
        return [
            Finding(
                "PYS000",
                path,
                f"cannot parse Python source: {error}",
                "restore readable Python",
            )
        ]
    rules = conventions.load_pack("python")
    allowed = set(as_list(options.get("symbol_exceptions", {}).get(path)))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name in allowed or any(
            re.fullmatch(p, node.name) for p in rules["framework_symbols"]
        ):
            continue
        casings = (
            rules["class"] if isinstance(node, ast.ClassDef) else rules["function"]
        )
        if not house_style.matches_casing(node.name, casings):
            found.append(
                convention(
                    path,
                    f"line {node.lineno}: symbol '{node.name}' is not {casing_label(casings)}",
                    "use PascalCase classes and snake_case functions",
                )
            )
    return found


def path_findings(
    repo: Path,
    path: str,
    config: dict[str, Any],
    *,
    existing_paths: set[str] | None = None,
) -> list[Finding]:
    options = settings(config, "naming")
    if conventions.ignored_path(path) or matches_any(
        path, as_list(options.get("skip"))
    ):
        return []
    existing_segments = path_prefixes(existing_paths or ())
    new_file = path not in existing_segments
    return [
        *word_findings(path, existing_segments),
        *version_findings(path, options, existing_segments),
        *parent_findings(path, existing_paths),
        *plural_findings(path, options, existing_segments),
        *casing_findings(repo, path, existing_segments),
        *(test_file_findings(path, config) if new_file else []),
        *(symbol_findings(repo, path, options) if new_file else []),
    ]


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        selected = scope.select(repo, argv)
        if not selected.paths:
            return []
        found = []
        with scope.checkout(repo, selected) as root:
            for raw in selected.paths:
                path = normalized_path(root, raw)
                if path is None:
                    found.append(
                        convention(
                            raw,
                            "path is outside the repository",
                            "name a repository path",
                        )
                    )
                else:
                    found.extend(
                        path_findings(
                            root,
                            path,
                            config,
                            existing_paths=selected.existing_paths,
                        )
                    )
        return house_style.apply_exceptions(found, config)
    except InventoryError as error:
        return [
            convention(".", str(error), "provide a readable Git work tree or snapshot")
        ]
