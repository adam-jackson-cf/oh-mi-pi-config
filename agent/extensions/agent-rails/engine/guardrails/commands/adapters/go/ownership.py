"""Which Go packages and import paths the config's public entry points own."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.adapters.go.syntax import SOURCE_SUFFIX
from guardrails.commands.adapters.go.toolchain import ToolchainError
from guardrails.context.config.reader import tests_settings

MODULE_LINE = re.compile(r"^module\s+(\S+)", re.M)


def module_of(repo: Path, path: str) -> tuple[str, str] | None:
    folder = PurePosixPath(path).parent
    while True:
        manifest = repo / folder / "go.mod"
        if manifest.is_file():
            match = MODULE_LINE.search(manifest.read_text(encoding="utf-8"))
            if match is None:
                raise ToolchainError(f"{manifest} names no module")
            return folder.as_posix(), match.group(1)
        if folder == PurePosixPath("."):
            return None
        folder = folder.parent


def owned_roots(config: dict[str, Any]) -> tuple[str, ...]:
    entries = tests_settings(config, "public_entry_points")
    roots = set()
    for raw in entries if isinstance(entries, dict) else ():
        path = str(raw).replace("\\", "/").rstrip("/")
        if path.endswith(SOURCE_SUFFIX):
            path = posixpath.dirname(path) or "."
        roots.add(posixpath.normpath(path))
    return tuple(sorted(roots))


def under(path: str, root: str) -> bool:
    return root == "." or path == root or path.startswith(root + "/")


def owned_imports(repo: Path, config: dict[str, Any]) -> tuple[str, ...]:
    prefixes = []
    for root in owned_roots(config):
        module = module_of(repo, posixpath.join(root, "x.go"))
        if module is None:
            continue
        folder, name = module
        relative = posixpath.relpath(root, folder)
        prefixes.append(name if relative == "." else f"{name}/{relative}")
    return tuple(sorted(prefixes))


def owned_import(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def owned_path(path: str, config: dict[str, Any]) -> bool:
    return any(under(path, root) for root in owned_roots(config))
