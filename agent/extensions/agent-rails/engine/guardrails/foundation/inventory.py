from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from guardrails.host.process import run_bytes

GLOB_CHARACTERS = frozenset("*?[")
# Rails state inside a checkout: the repo config and setup-repo-proofs run state. Like
# .git, these folders are never repository content, so no scan reports them.
RAILS_STATE_DIRS = (".agentrails", ".proofs")


def is_rails_state(path: str) -> bool:
    """The path is a rails state folder or inside one (.agentrails/, .proofs/)."""
    top = PurePosixPath(path).parts[:1]
    return bool(top) and top[0] in RAILS_STATE_DIRS


class InventoryError(Exception):
    pass


def repo_paths(repo: Path) -> list[str]:
    try:
        result = run_bytes(
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
            check=False,
        )
    except OSError as error:
        raise InventoryError(f"cannot run git: {error}") from error
    if result.returncode != 0:
        raise InventoryError(
            result.stderr.decode(errors="replace").strip() or "git ls-files failed"
        )
    return sorted({
        path
        for path in result.stdout.decode().split("\0")
        if path and not is_rails_state(path)
    })


def normalized_path(repo: Path, raw: str) -> str | None:
    if "\\" in raw:
        return None
    relative = PurePosixPath(raw)
    if relative.is_absolute():
        try:
            relative = PurePosixPath(
                Path(raw).resolve().relative_to(repo.resolve()).as_posix()
            )
        except ValueError:
            return None
    if ".." in relative.parts or not relative.parts or relative.parts == (".",):
        return None
    return relative.as_posix()


def matches(path: str, pattern: str) -> bool:
    if GLOB_CHARACTERS & set(pattern):
        return fnmatchcase(path, pattern)
    scope = pattern.rstrip("/")
    return path == scope or path.startswith(f"{scope}/")


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


def name_matches(path: str, pattern: str) -> bool:
    target = path if "/" in pattern else PurePosixPath(path).name
    return fnmatchcase(target, pattern)


def as_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def path_prefixes(paths: Iterable[str]) -> set[str]:
    prefixes: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        prefixes.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return prefixes
