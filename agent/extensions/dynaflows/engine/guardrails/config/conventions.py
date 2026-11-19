from __future__ import annotations

import fnmatch
import json
from functools import cache
from importlib import resources
from pathlib import PurePosixPath


@cache
def load_pack(name: str) -> dict:
    return json.loads(
        resources.files("guardrails.data").joinpath("conventions", f"{name}.json").read_bytes()
    )


@cache
def packs() -> tuple[dict, ...]:
    return tuple(load_pack(name) for name in ("python", "typescript", "go", "shell", "tooling"))


def for_path(path: str) -> dict:
    return next(
        (pack for pack in packs() if any(path.endswith(ext) for ext in pack["extensions"])),
        load_pack("tooling"),
    )


def ignored_path(path: str) -> bool:
    directories = PurePosixPath(path).parts[:-1]
    return any(folder in pack["ignored_folders"] for pack in packs() for folder in directories)


def fixed_file(path: str) -> bool:
    pure = PurePosixPath(path)
    pack = for_path(path)
    if any(
        fnmatch.fnmatchcase(pure.name, pattern)
        for rules in (pack, load_pack("tooling"))
        for pattern in rules["fixed_files"]
    ):
        return True
    return bool(
        pack.get("package_files")
        and pure.name in (f"{pure.parent.name}.go", f"{pure.parent.name}_test.go")
    )


def fixed_folder(path: str, name: str) -> bool:
    return any(name in pack["fixed_folders"] for pack in (for_path(path), load_pack("tooling")))


def test_patterns(path: str | None = None) -> list[str]:
    selected = packs() if path is None else (for_path(path),)
    return [pattern for pack in selected for pattern in pack["test_patterns"]]


def test_like_patterns(path: str) -> list[str]:
    return for_path(path)["test_like_patterns"]


def placement(path: str) -> dict:
    return for_path(path)["placement"]
