"""Repository path inventory and the path-matching rules shared by the structure checkers."""

from __future__ import annotations

import subprocess
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

GLOB_CHARACTERS = frozenset("*?[")


class InventoryError(Exception):
    """Git could not list the repository's paths."""


def repo_paths(repo: Path) -> list[str]:
    """Tracked files plus untracked, not-ignored files, as sorted repo-relative posix paths."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise InventoryError(f"cannot run git: {error}") from error
    if result.returncode != 0:
        raise InventoryError(
            result.stderr.decode(errors="replace").strip() or "git ls-files failed"
        )
    return sorted({path for path in result.stdout.decode().split("\0") if path})


def normalized_path(repo: Path, raw: str) -> str | None:
    """Resolve an inside-repo path to posix form, refusing traversal and foreign paths."""
    if "\\" in raw:
        return None
    relative = PurePosixPath(raw)
    if relative.is_absolute():
        try:
            relative = PurePosixPath(Path(raw).resolve().relative_to(repo.resolve()).as_posix())
        except ValueError:
            return None
    if ".." in relative.parts or not relative.parts or relative.parts == (".",):
        return None
    return relative.as_posix()


def matches(path: str, pattern: str) -> bool:
    """A glob pattern matches with fnmatch; a plain pattern matches the path or a parent."""
    if GLOB_CHARACTERS & set(pattern):
        return fnmatchcase(path, pattern)
    scope = pattern.rstrip("/")
    return path == scope or path.startswith(f"{scope}/")


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


def name_matches(path: str, pattern: str) -> bool:
    """Test file patterns without a slash match the file name; others match the path."""
    target = path if "/" in pattern else PurePosixPath(path).name
    return fnmatchcase(target, pattern)


def as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def settings(profile: dict, checker: str) -> dict:
    """Per-repo settings for one checker from the profile's ``guardrails`` section."""
    section = profile.get("guardrails")
    value = section.get(checker) if isinstance(section, dict) else None
    return value if isinstance(value, dict) else {}


def selected_paths(repo: Path, argv: list[str]) -> list[str]:
    """Paths named on the command line, or the whole inventory when none are named."""
    return list(argv) if argv else repo_paths(repo)
