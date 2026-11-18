"""NAME.conventions and NAME.test-files from the shipped house style plus profile settings.

Every path gets the house-style verdicts: banned words, ``v<N>`` tokens and a child repeating
its parent. Casing applies below the ecosystem roots the profile names. Test files match the
profile's ``layout.test_file_patterns`` and carry no ticket, version or date tokens.

Profile ``guardrails.naming`` settings (all optional):

- ``ecosystems``: ``{"python": ["src", "tests"], "typescript": [...], "markdown": [...],
  "asset_id": [".agents/skills"]}``: plain directory roots where each house-style casing applies
  to file stems and to directory segments below the root.
- ``skip``: path patterns excluded from naming (vendored or generated trees).
- ``version_exempt``: path patterns where names may hold versions (dated design records).
- ``standalone_versions``: overrides the house style's ``<name>/v<N>`` allowance.
- ``symbol_exceptions``: ``{"<path>": ["<name>", ...]}`` for externally fixed callback names.
- ``check_plurals``: also flag plural directory names outside the house-style catalogue list.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path, PurePosixPath

from omp_guardrails.config import house_style
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import (
    InventoryError,
    as_list,
    matches,
    matches_any,
    name_matches,
    normalized_path,
    selected_paths,
    settings,
)

CONVENTIONS = "NAME.conventions"
TEST_FILES = "NAME.test-files"


def casing_label(casings: list[str]) -> str:
    return " or ".join(casings)


def convention(path: str, message: str, fix: str) -> Finding:
    return Finding(CONVENTIONS, path, message, fix)


def word_findings(path: str) -> list[Finding]:
    parts = PurePosixPath(path).parts
    found = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
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


def version_findings(path: str, config: dict) -> list[Finding]:
    if matches_any(path, as_list(config.get("version_exempt"))):
        return []
    rules = house_style.load_house_style()["versioning"]
    standalone = config.get("standalone_versions", rules["standalone_allowed"])
    parts = PurePosixPath(path).parts
    for index, part in enumerate(parts):
        problem = house_style.version_problem(
            part, last=index == len(parts) - 1, standalone_allowed=bool(standalone)
        )
        if problem:
            return [convention(path, problem, "put versions in data or a <name>/v<N> folder")]
    return []


def parent_findings(path: str) -> list[Finding]:
    repeated = house_style.child_repeats_parent(PurePosixPath(path).parts)
    if repeated is None:
        return []
    return [
        convention(
            path, f"'{repeated}' repeats its parent folder's name", "name the child by its role"
        )
    ]


def plural_findings(path: str, config: dict) -> list[Finding]:
    if not config.get("check_plurals"):
        return []
    allowed = set(as_list(config.get("plural_allowlist")))
    return [
        convention(
            path, f"plural directory name '{part}'", "use a singular noun unless it is a catalogue"
        )
        for part in PurePosixPath(path).parts[:-1]
        if re.fullmatch(r"[a-z][a-z0-9_-]*[^s]s", part)
        and not house_style.is_catalogue_plural(part)
        and part not in allowed
    ]


def casing_findings(path: str, config: dict) -> list[Finding]:
    found = []
    ecosystems = config.get("ecosystems", {})
    for name, roots in ecosystems.items() if isinstance(ecosystems, dict) else []:
        rules = house_style.ecosystem(name)
        extensions = rules["extensions"]
        pure = PurePosixPath(path)
        if extensions and not any(pure.name.endswith(ext) for ext in extensions):
            continue
        for root in as_list(roots):
            if matches(path, root) and path != root.rstrip("/"):
                below = pure.relative_to(root.rstrip("/")).parts
                found.extend(segment_casing(path, name, rules, below, check_file=bool(extensions)))
    return found


def segment_casing(
    path: str, name: str, rules: dict, below: tuple[str, ...], *, check_file: bool
) -> list[Finding]:
    """Directory segments below an ecosystem root, then the file stem, against its casing."""
    found = [
        convention(path, f"{name} directory '{segment}' is not {label}", "rename the directory")
        for segment in below[:-1]
        if not segment.startswith(".")
        and not house_style.matches_casing(segment, rules["directory"])
        and (label := casing_label(rules["directory"]))
    ]
    file_name = below[-1]
    if check_file and not house_style.matches_casing(house_style.stem(file_name), rules["file"]):
        label = casing_label(rules["file"])
        found.append(
            convention(path, f"{name} file '{file_name}' is not {label}", "rename the file")
        )
    return found


def test_patterns(profile: dict) -> list[str]:
    layout = profile.get("layout")
    patterns = as_list(layout.get("test_file_patterns")) if isinstance(layout, dict) else []
    if patterns:
        return patterns
    defaults = house_style.load_house_style()["test_roots"]["file_patterns"]
    return [pattern for group in defaults.values() for pattern in group]


def is_test_file(path: str, profile: dict) -> bool:
    return any(name_matches(path, pattern) for pattern in test_patterns(profile))


def test_file_findings(path: str, profile: dict) -> list[Finding]:
    patterns = test_patterns(profile)
    test_like = house_style.load_house_style()["test_roots"]["test_like_patterns"]
    if not any(name_matches(path, pattern) for pattern in patterns):
        if any(name_matches(path, pattern) for pattern in test_like):
            return [
                Finding(
                    TEST_FILES,
                    path,
                    "test-like file matches no profile test file pattern, so the runner skips it",
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


def symbol_findings(repo: Path, path: str, config: dict) -> list[Finding]:
    roots = (
        config.get("ecosystems", {}).get("python")
        if isinstance(config.get("ecosystems"), dict)
        else None
    )
    if not path.endswith(".py") or not matches_any(path, as_list(roots)):
        return []
    source = repo / path
    if not source.is_file():
        return []
    try:
        tree = ast.parse(source.read_bytes(), filename=path)
    except (OSError, SyntaxError, ValueError) as error:
        return [
            Finding(
                "PYS000", path, f"cannot parse Python source: {error}", "restore readable Python"
            )
        ]
    rules = house_style.ecosystem("python")
    allowed = set(as_list(config.get("symbol_exceptions", {}).get(path)))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name in allowed or any(
            re.fullmatch(p, node.name) for p in rules["framework_symbols"]
        ):
            continue
        casings = rules["class"] if isinstance(node, ast.ClassDef) else rules["function"]
        if not house_style.matches_casing(node.name, casings):
            found.append(
                convention(
                    path,
                    f"line {node.lineno}: symbol '{node.name}' is not {casing_label(casings)}",
                    "use PascalCase classes and snake_case functions",
                )
            )
    return found


def path_findings(repo: Path, path: str, profile: dict) -> list[Finding]:
    """Every naming finding for one repository path, shared with the changed-path check."""
    config = settings(profile, "naming")
    if matches_any(path, as_list(config.get("skip"))):
        return []
    return [
        *word_findings(path),
        *version_findings(path, config),
        *parent_findings(path),
        *plural_findings(path, config),
        *casing_findings(path, config),
        *test_file_findings(path, profile),
        *symbol_findings(repo, path, config),
    ]


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    try:
        raw_paths = selected_paths(repo, argv)
    except InventoryError as error:
        return [convention(".", str(error), "run inside a Git work tree")]
    found = []
    for raw in raw_paths:
        path = normalized_path(repo, raw)
        if path is None:
            found.append(
                convention(raw, "path is outside the repository", "name a repository path")
            )
        else:
            found.extend(path_findings(repo, path, profile))
    return house_style.apply_exceptions(found, profile)
