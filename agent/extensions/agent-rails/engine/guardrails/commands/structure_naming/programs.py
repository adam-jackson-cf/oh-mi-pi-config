"""What makes a source file a program, in each structure language, and where a
repository declares or launches its programs: the facts the consumer proposer reads
(structure-languages.json proposers.signals, STRUCT.consumer-candidates)."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from functools import cache
from pathlib import PurePosixPath
from typing import Any

from guardrails.foundation.source.modules import folder_of, joined, script_file
from guardrails.foundation.source.tree import SourceError, structure_languages

RULE = "STRUCT.consumer-candidates"
COMMANDS = "cmd"
PAGE_SUFFIXES = frozenset({".html", ".htm"})
PAGE_SCRIPT = re.compile(
    r"""<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE
)
COMMAND_WORD = re.compile(r"""[^\s"'`;&|()<>]+""")


def signals(language: str) -> dict[str, Any]:
    found: dict[str, Any] = structure_languages()["proposers"]["signals"][RULE][
        language
    ]
    return found


@cache
def marker_patterns(language: str) -> tuple[tuple[str, re.Pattern[str]], ...]:
    return tuple(
        (name, re.compile(pattern))
        for name, pattern in signals(language)["run_markers"].items()
    )


def run_markers(language: str, source: str) -> list[str]:
    """The run markers of a language whose pattern a file's source matches."""
    return [
        name for name, pattern in marker_patterns(language) if pattern.search(source)
    ]


def ui_app_markers(language: str) -> frozenset[str]:
    return frozenset(signals(language)["ui_app_markers"])


def ui_libraries(language: str) -> frozenset[str]:
    return frozenset(signals(language)["ui_libraries"])


def package_file(language: str) -> str:
    """The file that makes a folder a package (Python), an npm package or a Go module."""
    name: str = signals(language)["package_file"]
    return name


def command_manifests(language: str) -> frozenset[str]:
    return frozenset(signals(language)["command_manifests"])


def package_run_module(language: str) -> str | None:
    """The file name a package runs as itself (__main__.py), None for a language with
    none."""
    name: str | None = signals(language)["package_run_module"]
    return name


def manifest_data(manifest: str, text: str) -> dict[str, Any]:
    """A package.json file's object; SourceError when it is not one."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SourceError(f"cannot parse {manifest}: {error}") from error
    if not isinstance(data, dict):
        raise SourceError(f"{manifest} must hold a JSON object")
    return data


def manifest_entries(manifest: str, text: str) -> list[tuple[str, str]]:
    """A package.json file's bin and main paths, each with its field; SourceError when
    the file or a field is malformed."""
    data = manifest_data(manifest, text)
    found: list[tuple[str, str]] = []
    commands, main = data.get("bin"), data.get("main")
    if isinstance(commands, str):
        found.append(("bin", commands))
    elif isinstance(commands, dict) and all(
        isinstance(value, str) for value in commands.values()
    ):
        found.extend(("bin", value) for value in commands.values())
    elif commands is not None:
        raise SourceError(f"{manifest}: bin must be a path or a map of names to paths")
    if isinstance(main, str):
        found.append(("main", main))
    elif main is not None:
        raise SourceError(f"{manifest}: main must be a path")
    return found


def script_commands(manifest: str, text: str) -> list[str]:
    """A package.json file's scripts commands; SourceError when scripts is not a map of
    names to commands."""
    scripts = manifest_data(manifest, text).get("scripts", {})
    if not isinstance(scripts, dict) or not all(
        isinstance(command, str) for command in scripts.values()
    ):
        raise SourceError(f"{manifest}: scripts must map names to commands")
    return list(scripts.values())


def named_files(
    folder: str, words: Collection[str], tracked: Collection[str]
) -> set[str]:
    """The tracked source files that words name by a path from a folder, a TypeScript
    file standing for the .js path that names it."""
    found = set()
    for word in words:
        base = joined(folder, word.removeprefix("/"))
        target = None if base is None else script_file(base, tracked)
        if target is not None:
            found.add(target)
    return found


def declared_scripts(manifest: str, text: str, tracked: Collection[str]) -> set[str]:
    """The files a package.json declares as the package's commands (bin)."""
    return named_files(
        folder_of(manifest),
        [value for field, value in manifest_entries(manifest, text) if field == "bin"],
        tracked,
    )


def launched_scripts(manifest: str, text: str, tracked: Collection[str]) -> set[str]:
    """The files a package.json scripts command names by path from the manifest's
    folder."""
    words = [
        word
        for command in script_commands(manifest, text)
        for word in COMMAND_WORD.findall(command)
    ]
    return named_files(folder_of(manifest), words, tracked)


def page_scripts(page: str, text: str, tracked: Collection[str]) -> set[str]:
    """The files an HTML page loads by <script src>, from the page's folder (a leading /
    is the page's folder, as a dev server serving it reads it)."""
    return named_files(folder_of(page), PAGE_SCRIPT.findall(text), tracked)


def is_page(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in PAGE_SUFFIXES


def command_folder(folder: str, module: str) -> bool:
    """Whether a folder is where a Go module keeps a command: the module root or
    cmd/<name>/ directly under it."""
    parts = PurePosixPath(folder).relative_to(module).parts
    return not parts or (len(parts) == 2 and parts[0] == COMMANDS)
