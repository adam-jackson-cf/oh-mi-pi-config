"""Profile settings, maintained-file discovery and root matching for the Python policy checkers.

Every per-repo value lives in the profile's ``guardrails.python`` section. Defaults grant no
exceptions: an unset allowlist approves nothing, and an unset scope falls back to the
maintained roots, so a missing setting fails closed instead of silently skipping a rule.

Root and path patterns use one syntax. A plain pattern (``src/shop``) matches that path and
everything below it. A pattern with ``*``, ``?`` or ``[`` matches segment by segment, so
``lab/stages/*/*/tool/src`` names one source root per stage.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import (
    GLOB_CHARACTERS,
    InventoryError,
    as_list,
    matches_any,
    normalized_path,
    repo_paths,
    settings,
)

CHECKER = "python"
# Generated, cached and dependency trees are never maintained source, in any repository.
BUNDLED_EXCLUSIONS = (
    ".build",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "venv",
)


@dataclass(frozen=True)
class PythonSettings:
    """The ``guardrails.python`` section with typed accessors and fail-closed defaults."""

    raw: dict

    def paths(self, key: str) -> list[str]:
        return [item.strip("/") for item in as_list(self.raw.get(key)) if item.strip("/")]

    def scope(self, key: str) -> list[str]:
        """A rule scope; unset means every maintained root."""
        return self.paths(key) if key in self.raw else self.paths("maintained_roots")

    def mapping(self, key: str) -> dict:
        value = self.raw.get(key)
        return value if isinstance(value, dict) else {}

    def number(self, section: str, key: str, default: int | None) -> int | None:
        value = self.mapping(section).get(key, default) if section else self.raw.get(key, default)
        return value if isinstance(value, int) and not isinstance(value, bool) else default


def load_settings(profile: dict) -> PythonSettings:
    return PythonSettings(settings(profile, CHECKER))


def segments(path: str) -> tuple[str, ...]:
    return PurePosixPath(path).parts


def root_depth(relpath: str, pattern: str) -> int | None:
    """Number of leading segments ``pattern`` covers in ``relpath``, or None if it misses."""
    wanted = segments(pattern)
    parts = segments(relpath)
    if not wanted or len(parts) < len(wanted):
        return None
    if GLOB_CHARACTERS & set(pattern):
        hit = all(fnmatchcase(part, want) for part, want in zip(parts, wanted, strict=False))
    else:
        hit = parts[: len(wanted)] == wanted
    return len(wanted) if hit else None


def matched_root(relpath: str, roots: list[str]) -> int | None:
    """Depth of the deepest root containing ``relpath``, or None when no root does."""
    depths = [depth for root in roots if (depth := root_depth(relpath, root)) is not None]
    return max(depths) if depths else None


def in_roots(relpath: str, roots: list[str]) -> bool:
    return matched_root(relpath, roots) is not None


def is_excluded(relpath: str, config: PythonSettings) -> bool:
    parts = segments(relpath)
    if "__pycache__" in parts or (parts and parts[0] in BUNDLED_EXCLUSIONS):
        return True
    return matches_any(relpath, config.paths("excluded_paths"))


def parse_paths(command: str, argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(prog=f"omp-guardrails {command}")
    parser.add_argument("paths", nargs="*", help="limit the check to these files or directories")
    return parser.parse_args(argv).paths


def python_files(repo: Path, config: PythonSettings, selected: list[str]) -> list[str]:
    """Inventory ``.py`` files outside the exclusions, limited to ``selected`` when given.

    Raises InventoryError when the repository is not a Git work tree.
    """
    scope = [path for raw in selected if (path := normalized_path(repo, raw)) is not None]
    return [
        path
        for path in repo_paths(repo)
        if path.endswith(".py")
        and (repo / path).is_file()
        and not is_excluded(path, config)
        and (not selected or matches_any(path, scope))
    ]


@dataclass(frozen=True)
class Source:
    """A parsed maintained file, or the reason it could not be read."""

    relpath: str
    text: str = ""
    tree: ast.Module | None = None
    error: str = ""


def read_source(repo: Path, relpath: str) -> Source:
    try:
        text = (repo / relpath).read_text(encoding="utf-8")
        return Source(relpath, text, ast.parse(text, filename=relpath))
    except UnicodeDecodeError as error:
        return Source(relpath, error=f"could not decode source: {error.reason}")
    except SyntaxError as error:
        return Source(relpath, error=f"line {error.lineno or 1}: could not parse: {error.msg}")


def finding(rule: str, relpath: str, line: int | None, message: str, fix: str) -> Finding:
    """Code findings name the line inside the message so the path stays a plain repo path."""
    prefix = f"line {line}: " if line is not None else ""
    return Finding(rule, relpath, f"{prefix}{message}", fix)


def inventory_finding(rule: str, error: InventoryError) -> Finding:
    return Finding(rule, ".", str(error), "run inside a Git work tree")


def setting_finding(rule: str, key: str, why: str) -> Finding:
    return Finding(
        rule,
        PROFILE_PATH,
        f"guardrails.{CHECKER}.{key} {why}",
        f"set guardrails.{CHECKER}.{key} in the profile front matter",
    )
