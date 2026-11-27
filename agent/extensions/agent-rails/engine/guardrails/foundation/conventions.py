from __future__ import annotations

import fnmatch
import json
from functools import cache
from pathlib import Path, PurePosixPath
from typing import Any

DATA_FOLDER = Path(__file__).resolve().parents[2] / "data"


@cache
def load_pack(name: str) -> dict[str, Any]:
    pack: dict[str, Any] = json.loads(
        (DATA_FOLDER / "conventions" / f"{name}.json").read_bytes()
    )
    return pack


@cache
def packs() -> tuple[dict[str, Any], ...]:
    return tuple(
        load_pack(name) for name in ("python", "typescript", "go", "shell", "tooling")
    )


def for_path(path: str) -> dict[str, Any]:
    return next(
        (
            pack
            for pack in packs()
            if any(path.endswith(ext) for ext in pack["extensions"])
        ),
        load_pack("tooling"),
    )


@cache
def data(name: str) -> Any:
    """An engine data file (engine/data/<name>), read as JSON."""
    return json.loads((DATA_FOLDER / name).read_bytes())


@cache
def ignored_folders() -> frozenset[str]:
    """The folders no scan reads, whatever language a file in them is: every pack's
    installed, generated and tool-state folders."""
    return frozenset(folder for pack in packs() for folder in pack["ignored_folders"])


def ignored_path(path: str) -> bool:
    """Whether a file sits in a folder no scan reads: one of ignored_folders, or a folder
    its own language's tools write output to (Python's build/, a bundler's dist/) or its
    toolchain ignores (Go's testdata/). Those names are only ignored for that language:
    a Python package may be called coverage."""
    pack = for_path(path)
    own = {
        *pack.get("output_folders", []),
        *pack.get("build_ignored", {}).get("folders", []),
    }
    return any(
        folder in ignored_folders() or folder in own
        for folder in PurePosixPath(path).parts[:-1]
    )


def runtime_extensions(language: str) -> tuple[str, ...]:
    """A language's source extensions without its stub extension: the files that run."""
    pack = load_pack(language)
    return tuple(
        extension
        for extension in pack["extensions"]
        if extension != pack.get("stub_extension")
    )


def test_support_file(path: str) -> bool:
    """A file a test runner reads as test support by name (pytest's conftest.py)."""
    return PurePosixPath(path).name in for_path(path).get("test_support_files", [])


def test_frameworks(language: str) -> dict[str, Any]:
    """How a language's test frameworks mark tests: modules, case classes and the name
    prefixes of test classes and functions."""
    frameworks: dict[str, Any] = load_pack(language)["test_frameworks"]
    return frameworks


def fixed_file(path: str) -> bool:
    pure = PurePosixPath(path)
    pack = for_path(path)
    if any(
        fnmatch.fnmatchcase(pure.name, pattern)
        for rules in (pack, load_pack("tooling"))
        for pattern in rules["fixed_files"]
    ):
        return True
    package = pure.parent.name
    return bool(pack.get("package_files")) and pure.name in {
        *(package + extension for extension in pack["extensions"]),
        *(pattern.replace("*", package, 1) for pattern in pack["test_patterns"]),
    }


def comment_syntax(path: str) -> dict[str, Any]:
    """How a file writes comments: its line markers, its block marker pairs, the quotes
    of its string literals and, for shell, the characters a line marker must follow to
    open a comment (word_delimiters). A language pack names one syntax for all its
    files; the tooling pack names markers per file pattern, matched case-insensitively,
    and a file several patterns match has all their markers. Markup has no strings."""
    syntax: dict[str, Any] = for_path(path)["comment_syntax"]
    if "files" not in syntax:
        return syntax
    name = PurePosixPath(path).name.lower()

    def named(patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(name, pattern.lower()) for pattern in patterns)

    entries = [entry for entry in syntax["files"] if named(entry["patterns"])]
    return {
        "line": [marker for entry in entries for marker in entry.get("line", [])],
        "block": [pair for entry in entries for pair in entry.get("block", [])],
        "quotes": [] if named(syntax["markup"]) else syntax["quotes"],
    }


def parser_directive_file(path: str) -> bool:
    """Whether a file's leading comment lines may be parser directives (a Dockerfile's
    # syntax= and # escape=)."""
    name = PurePosixPath(path).name.lower()
    return any(
        fnmatch.fnmatchcase(name, pattern.lower())
        for pattern in load_pack("tooling")["comment_syntax"]["parser_directives"]
    )


def fixed_folder(path: str, name: str) -> bool:
    return any(
        name in pack["fixed_folders"] for pack in (for_path(path), load_pack("tooling"))
    )


def test_patterns(path: str) -> list[str]:
    patterns: list[str] = for_path(path)["test_patterns"]
    return patterns


def test_like_patterns(path: str) -> list[str]:
    patterns: list[str] = for_path(path)["test_like_patterns"]
    return patterns


def placement(path: str) -> dict[str, Any]:
    rules: dict[str, Any] = for_path(path)["placement"]
    return rules
