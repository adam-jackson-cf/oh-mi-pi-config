"""Shared maintained-Python scope rules for repository quality gates."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExclusionRule:
    """One deterministic non-maintained path exclusion."""

    pattern: str
    rationale: str


EXCLUSION_RULES: tuple[ExclusionRule, ...] = (
    ExclusionRule(".build/", "rendered generated output"),
    ExclusionRule(".git/", "git metadata"),
    ExclusionRule(".mypy_cache/", "tool cache"),
    ExclusionRule(".pytest_cache/", "tool cache"),
    ExclusionRule(".ruff_cache/", "tool cache"),
    ExclusionRule(".tox/", "tool environment"),
    ExclusionRule(".venv/", "virtual environment"),
    ExclusionRule("__pycache__/", "Python bytecode cache"),
    ExclusionRule("build/", "build output"),
    ExclusionRule("dist/", "distribution output"),
    ExclusionRule("node_modules/", "vendored dependency tree"),
    ExclusionRule("venv/", "virtual environment"),
)

# Per-target configuration: enumerate every maintained Python source root.
# Empty roots deliberately classify discovered Python files as unmapped (PYQ100).
MAINTAINED_ROOTS: tuple[str, ...] = ()
# Per-target skill directory roots, used only where the target maintains skills.
SKILL_ROOTS: tuple[str, ...] = ()


def repo_relative_path(repo_root: Path, path: Path) -> str:
    """Return a normalized repository-relative path."""
    resolved_root = repo_root.resolve()
    resolved_path = path.resolve()
    return resolved_path.relative_to(resolved_root).as_posix()


def path_matches_prefix(relpath: str, prefix: str) -> bool:
    """Return whether relpath is the prefix itself or sits below it."""
    normalized_prefix = prefix.rstrip("/")
    return relpath == normalized_prefix or relpath.startswith(f"{normalized_prefix}/")


def exclusion_for_relpath(relpath: str) -> ExclusionRule | None:
    """Return the deterministic exclusion rule for a repository-relative path."""
    normalized = relpath.removeprefix("./")
    for rule in EXCLUSION_RULES:
        pattern = rule.pattern.rstrip("/")
        if path_matches_prefix(normalized, pattern):
            return rule
    if "/__pycache__/" in f"/{normalized}/":
        return ExclusionRule("__pycache__/", "Python bytecode cache")
    return None


def is_excluded_relpath(relpath: str) -> bool:
    """Return whether a repository-relative path is excluded from quality gates."""
    return exclusion_for_relpath(relpath) is not None


def is_maintained_python_relpath(relpath: str) -> bool:
    """Return whether a repository-relative Python path is maintained source."""
    normalized = relpath.removeprefix("./")
    if not normalized.endswith(".py"):
        return False
    if any(path_matches_prefix(normalized, root) for root in SKILL_ROOTS):
        return (
            "/scripts/" in normalized
            and "/.build/" not in normalized
            and not is_excluded_relpath(normalized)
        )
    if is_excluded_relpath(normalized):
        return False
    return any(path_matches_prefix(normalized, root) for root in MAINTAINED_ROOTS)


def discover_unexcluded_python_files(
    repo_root: Path, search_roots: Sequence[Path] | None = None
) -> list[Path]:
    """Discover in-repository Python files without traversing excluded trees."""
    lexical_root = repo_root.absolute()
    resolved_root = repo_root.resolve()
    roots = search_roots if search_roots is not None else (repo_root,)
    files: set[Path] = set()
    for search_root in roots:
        if not _is_unexcluded_path(lexical_root, resolved_root, search_root):
            continue
        if search_root.is_file():
            candidates = (search_root,) if search_root.suffix == ".py" else ()
        elif search_root.is_dir():
            candidates = _walk_unexcluded_python_files(
                lexical_root, resolved_root, search_root
            )
        else:
            candidates = ()
        files.update(
            candidate
            for candidate in candidates
            if _is_unexcluded_path(lexical_root, resolved_root, candidate)
        )
    return sorted(files)


def _is_unexcluded_path(lexical_root: Path, resolved_root: Path, path: Path) -> bool:
    try:
        lexical_relpath = path.absolute().relative_to(lexical_root).as_posix()
    except ValueError:
        return False
    if exclusion_for_relpath(lexical_relpath) is not None:
        return False
    try:
        resolved_relpath = path.resolve().relative_to(resolved_root).as_posix()
    except ValueError:
        return False
    return exclusion_for_relpath(resolved_relpath) is None


def _walk_unexcluded_python_files(
    lexical_root: Path, resolved_root: Path, search_root: Path
) -> list[Path]:
    files: list[Path] = []
    for current_root, directory_names, file_names in os.walk(search_root):
        current_path = Path(current_root)
        directory_names[:] = [
            name
            for name in directory_names
            if _is_unexcluded_path(lexical_root, resolved_root, current_path / name)
        ]
        files.extend(current_path / name for name in file_names if name.endswith(".py"))
    return files


def discover_maintained_python_files(repo_root: Path) -> list[Path]:
    """Discover maintained Python files under the repository root."""
    return [
        path
        for path in discover_unexcluded_python_files(repo_root)
        if is_maintained_python_relpath(repo_relative_path(repo_root, path))
    ]
