from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.context.config.reader import checker_settings
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding, config_label
from guardrails.foundation.inventory import (
    GLOB_CHARACTERS,
    InventoryError,
    as_list,
    matches_any,
    normalized_path,
    repo_paths,
)
from guardrails.foundation.source.python import cpython_module
from guardrails.foundation.source.tree import SourceError, language_of, read

CHECKER = "python"


@dataclass(frozen=True)
class PythonSettings:
    raw: dict[str, Any]

    def paths(self, key: str) -> list[str]:
        return [
            item.strip("/") for item in as_list(self.raw.get(key)) if item.strip("/")
        ]

    def scope(self, key: str) -> list[str]:
        return self.paths(key) if key in self.raw else self.paths("maintained_roots")

    def mapping(self, key: str) -> dict[str, Any]:
        value = self.raw.get(key)
        return value if isinstance(value, dict) else {}

    def number(self, section: str, key: str, default: int | None) -> int | None:
        value = (
            self.mapping(section).get(key, default)
            if section
            else self.raw.get(key, default)
        )
        return (
            value if isinstance(value, int) and not isinstance(value, bool) else default
        )


def load_settings(config: dict[str, Any]) -> PythonSettings:
    return PythonSettings(checker_settings(config, CHECKER))


def segments(path: str) -> tuple[str, ...]:
    return PurePosixPath(path).parts


def root_depth(relpath: str, pattern: str) -> int | None:
    wanted = segments(pattern)
    parts = segments(relpath)
    if not wanted or len(parts) < len(wanted):
        return None
    if GLOB_CHARACTERS & set(pattern):
        hit = all(
            fnmatchcase(part, want) for part, want in zip(parts, wanted, strict=False)
        )
    else:
        hit = parts[: len(wanted)] == wanted
    return len(wanted) if hit else None


def matched_root(relpath: str, roots: list[str]) -> int | None:
    depths = [
        depth for root in roots if (depth := root_depth(relpath, root)) is not None
    ]
    return max(depths) if depths else None


def in_roots(relpath: str, roots: list[str]) -> bool:
    return matched_root(relpath, roots) is not None


def is_excluded(relpath: str, options: PythonSettings) -> bool:
    """A path in a folder no scan reads (installed, generated or tool-state code) or
    under the configured excluded_paths."""
    return conventions.ignored_path(relpath) or matches_any(
        relpath, options.paths("excluded_paths")
    )


def python_source(path: str) -> bool:
    """A Python source file, stubs included: the Python checks read both."""
    return language_of(path) == CHECKER


def parse_paths(command: str, argv: list[str]) -> list[str]:
    parser = argparse.ArgumentParser(prog=f"guardrails {command}")
    parser.add_argument(
        "paths", nargs="*", help="limit the check to these files or directories"
    )
    paths: list[str] = parser.parse_args(argv).paths
    return paths


def python_files(repo: Path, options: PythonSettings, selected: list[str]) -> list[str]:
    scope = [
        path for raw in selected if (path := normalized_path(repo, raw)) is not None
    ]
    return [
        path
        for path in repo_paths(repo)
        if python_source(path)
        and (repo / path).is_file()
        and not is_excluded(path, options)
        and (not selected or matches_any(path, scope))
    ]


@dataclass(frozen=True)
class Source:
    relpath: str
    text: str = ""
    tree: ast.Module | None = None
    error: str = ""


def read_source(repo: Path, relpath: str) -> Source:
    try:
        text = read(repo, relpath)
        return Source(relpath, text, cpython_module(relpath, text))
    except SourceError as error:
        return Source(relpath, error=str(error))


def finding(
    rule: str, relpath: str, line: int | None, message: str, fix: str
) -> Finding:
    prefix = f"line {line}: " if line is not None else ""
    return Finding(rule, relpath, f"{prefix}{message}", fix)


def inventory_finding(rule: str, error: InventoryError) -> Finding:
    return Finding(rule, ".", str(error), "run inside a Git work tree")


def setting_finding(rule: str, key: str, why: str) -> Finding:
    return Finding(
        rule,
        config_label("checkers"),
        f"checkers.{CHECKER}.{key} {why}",
        f"set checkers.{CHECKER}.{key} in .agentrails/config.json",
    )
