from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from guardrails.adapters.go.process import run_command
from guardrails.adapters.go.source import SourceError, defined_tests

TIMEOUT_S = 300
TEST_FILE_KEYS = ("TestGoFiles", "XTestGoFiles")
BUILD_FLAGS = frozenset({
    "-tags",
    "-mod",
    "-race",
    "-cover",
    "-covermode",
    "-coverpkg",
    "-modfile",
    "-gcflags",
    "-ldflags",
    "-trimpath",
    "-buildvcs",
})


@dataclass(frozen=True)
class Package:
    import_path: str
    directory: str
    tests: dict[str, str]


def build_arguments(arguments: list[str]) -> list[str]:
    chosen = []
    index = 0
    while index < len(arguments):
        flag, equals, _ = arguments[index].partition("=")
        if flag in BUILD_FLAGS:
            chosen.append(arguments[index])
            if (
                not equals
                and flag not in {"-race", "-cover", "-trimpath"}
                and index + 1 < len(arguments)
            ):
                index += 1
                chosen.append(arguments[index])
        index += 1
    return chosen


def decode_objects(text: str) -> list[dict[str, object]]:
    decoder = json.JSONDecoder()
    found = []
    index = 0
    while index < len(text):
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        value, index = decoder.raw_decode(text, index)
        if isinstance(value, dict):
            found.append(value)
    return found


def list_packages(
    go: list[str],
    repo: Path,
    patterns: list[str],
    build: list[str],
    environment: Mapping[str, str],
) -> dict[str, Package]:
    result = run_command(
        [
            *go,
            "list",
            "-json=ImportPath,Dir,TestGoFiles,XTestGoFiles",
            *build,
            *patterns,
        ],
        repo,
        environment,
        TIMEOUT_S,
    )
    if result.returncode:
        tail = " ".join((result.stdout + result.stderr).split())[-600:]
        raise SourceError(f"go list exited {result.returncode}: {tail}")
    root = repo.resolve()
    packages = {}
    for item in decode_objects(result.stdout):
        package = described_package(item, root)
        packages[package.import_path] = package
    return packages


def file_names(item: dict[str, object], key: str) -> list[object]:
    value = item.get(key)
    return value if isinstance(value, list) else []


def described_package(item: dict[str, object], root: Path) -> Package:
    directory = Path(str(item.get("Dir", ""))).resolve()
    relative = (
        directory.relative_to(root).as_posix()
        if directory.is_relative_to(root)
        else directory.as_posix()
    )
    names = [str(name) for key in TEST_FILE_KEYS for name in file_names(item, key)]
    tests: dict[str, str] = {}
    for name in names:
        path = f"{relative}/{name}" if relative != "." else name
        for test in defined_tests((directory / name).read_text(encoding="utf-8")):
            tests.setdefault(test, path)
    return Package(str(item.get("ImportPath", "")), relative, tests)
