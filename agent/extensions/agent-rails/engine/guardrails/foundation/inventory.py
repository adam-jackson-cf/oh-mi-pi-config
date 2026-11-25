from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

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


def config_section(config: dict[str, Any], name: str) -> dict[str, Any]:
    """One top-level section of the repo's .agentrails/config.json, {} when absent."""
    value = config.get(name)
    return value if isinstance(value, dict) else {}


def settings(config: dict[str, Any], checker: str) -> dict[str, Any]:
    """checkers.<checker>: one checker's settings."""
    value = config_section(config, "checkers").get(checker)
    return value if isinstance(value, dict) else {}


def tests_settings(config: dict[str, Any], key: str) -> Any:
    """tests.<key>: the test-guidance settings (tiers, adapters, layout, ...)."""
    return config_section(config, "tests").get(key)


def structure_settings(config: dict[str, Any]) -> dict[str, Any]:
    """structure: owners, tiers, import_direction and ownership_areas."""
    return config_section(config, "structure")


def house_style_version(config: dict[str, Any]) -> object:
    return config_section(config, "houseStyle").get("version")


def config_exceptions(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The owner-approved exceptions: {rule, path, check?, reason, approvedBy, approvedAt}."""
    entries = config.get("exceptions")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def approvals(config: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """The owner approvals of one kind (budget, ratchet, skip or exemption)."""
    entries = config.get("approvals")
    return [
        entry
        for entry in (entries if isinstance(entries, list) else [])
        if isinstance(entry, dict) and entry.get("kind") == kind
    ]


def approved_targets(config: dict[str, Any], kind: str) -> set[str]:
    """The targets of one kind's approvals that carry an approver and a reason."""
    return {
        entry["target"]
        for entry in approvals(config, kind)
        if isinstance(entry.get("target"), str)
        and entry["target"].strip()
        and all(
            isinstance(entry.get(key), str) and entry[key].strip()
            for key in ("reason", "approvedBy")
        )
    }


def path_prefixes(paths: Iterable[str]) -> set[str]:
    prefixes: set[str] = set()
    for path in paths:
        parts = PurePosixPath(path).parts
        prefixes.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return prefixes
