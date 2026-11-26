from __future__ import annotations

import importlib.util
import json
import posixpath
import re
from collections.abc import Container
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from guardrails.foundation.source.python import PythonImport
from guardrails.foundation.source.tree import SourceError, languages

SCRIPT_SOURCES = {
    ".js": (".ts", ".tsx"),
    ".jsx": (".tsx",),
    ".mjs": (".mts",),
    ".cjs": (".cts",),
}
JSON_STRING = r'("(?:[^"\\]|\\.)*")'
JSONC_COMMENT = re.compile(JSON_STRING + r"|//[^\n]*|/\*.*?\*/", re.DOTALL)
JSONC_TRAILING_COMMA = re.compile(JSON_STRING + r"|,(?=\s*[}\]])")
MODULE_DIRECTIVE = re.compile(r"^\s*module\s+(\S+)", re.MULTILINE)


def python_edges(imports: list[PythonImport], module: str, package: str) -> list[str]:
    parent = (
        module.removesuffix(".__init__")
        if module.endswith(".__init__")
        else module.rpartition(".")[0]
    )
    edges: list[str] = []
    for item in imports:
        if item.statement == "import":
            edges.append(item.module)
            continue
        target = item.module
        if item.level:
            try:
                target = importlib.util.resolve_name("." * item.level + target, parent)
            except ImportError as error:
                raise SourceError(f"line {item.line}: {error}") from error
        edges.extend(
            target if name == "*" else f"{target}.{name}" for name in item.names
        )
    return [edge for edge in edges if edge == package or edge.startswith(f"{package}.")]


def folder_of(path: str) -> str:
    return posixpath.dirname(path) or "."


def joined(folder: str, relative: str) -> str | None:
    path = posixpath.normpath(posixpath.join(folder, relative))
    return None if path == ".." or path.startswith("../") else path


def nearest(path: str, name: str, tracked: Container[str]) -> str | None:
    folder = folder_of(path)
    while True:
        candidate = name if folder == "." else f"{folder}/{name}"
        if candidate in tracked:
            return candidate
        if folder == ".":
            return None
        folder = folder_of(folder)


@dataclass(frozen=True)
class TsConfig:
    folder: str
    base_url: str | None
    paths: tuple[tuple[str, tuple[str, ...]], ...]


def kept_string(match: re.Match[str]) -> str:
    return match.group(1) or ""


def jsonc(text: str) -> Any:
    without_comments = JSONC_COMMENT.sub(kept_string, text)
    return json.loads(JSONC_TRAILING_COMMA.sub(kept_string, without_comments))


def compiler_options(path: str, text: str) -> dict[str, Any]:
    try:
        data = jsonc(text)
    except ValueError as error:
        raise SourceError(f"{path}: {error}") from error
    options = data.get("compilerOptions", {}) if isinstance(data, dict) else None
    if not isinstance(options, dict):
        raise SourceError(f"{path}: compilerOptions must be an object")
    return options


def path_mappings(path: str, paths: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(paths, dict) or not all(
        isinstance(targets, list) and all(isinstance(item, str) for item in targets)
        for targets in paths.values()
    ):
        raise SourceError(f"{path}: compilerOptions.paths must map patterns to lists")
    return tuple((pattern, tuple(targets)) for pattern, targets in paths.items())


def tsconfig(path: str, text: str) -> TsConfig:
    options = compiler_options(path, text)
    folder = folder_of(path)
    base_url = options.get("baseUrl")
    if base_url is not None and not isinstance(base_url, str):
        raise SourceError(f"{path}: compilerOptions.baseUrl must be a string")
    return TsConfig(
        folder,
        None if base_url is None else joined(folder, base_url),
        path_mappings(path, options.get("paths", {})),
    )


def script_file(base: str, tracked: Container[str]) -> str | None:
    extensions = languages()["typescript"]
    stem, suffix = posixpath.splitext(base)
    candidates = [
        base,
        *(stem + source for source in SCRIPT_SOURCES.get(suffix, ())),
        *(base + extension for extension in extensions),
        *(f"{base}/index{extension}" for extension in extensions),
    ]
    return next((path for path in candidates if path in tracked), None)


def pattern_capture(pattern: str, specifier: str) -> str | None:
    if "*" not in pattern:
        return "" if pattern == specifier else None
    prefix, _, suffix = pattern.partition("*")
    fits = (
        len(specifier) >= len(prefix) + len(suffix)
        and specifier.startswith(prefix)
        and specifier.endswith(suffix)
    )
    return specifier[len(prefix) : len(specifier) - len(suffix)] if fits else None


def mapped_targets(config: TsConfig, specifier: str) -> list[str]:
    matches = [
        (pattern, capture, targets)
        for pattern, targets in config.paths
        if (capture := pattern_capture(pattern, specifier)) is not None
    ]
    if not matches:
        return []
    _, capture, targets = max(
        matches,
        key=lambda match: ("*" not in match[0], len(match[0].partition("*")[0])),
    )
    root = config.base_url or config.folder
    return [
        path
        for target in targets
        if (path := joined(root, target.replace("*", capture))) is not None
    ]


def resolve_specifier(
    importer: str, specifier: str, tracked: Container[str], config: TsConfig | None
) -> str | None:
    if specifier in {".", ".."} or specifier.startswith(("./", "../")):
        base = joined(folder_of(importer), specifier)
        return script_file(base, tracked) if base is not None else None
    if config is None:
        return None
    bases = mapped_targets(config, specifier)
    if (
        config.base_url is not None
        and (base := joined(config.base_url, specifier)) is not None
    ):
        bases.append(base)
    return next(
        (path for base in bases if (path := script_file(base, tracked)) is not None),
        None,
    )


def go_module_path(text: str) -> str:
    match = MODULE_DIRECTIVE.search(text)
    if match is None:
        raise SourceError("go.mod has no module directive")
    return match.group(1).strip('"`')


def package_import_path(module_path: str, module_folder: str, folder: str) -> str:
    try:
        relative = PurePosixPath(folder).relative_to(module_folder).as_posix()
    except ValueError as error:
        raise SourceError(
            f"{folder} is outside the Go module at {module_folder}"
        ) from error
    return module_path if relative == "." else f"{module_path}/{relative}"


def package_folder(
    module_path: str, module_folder: str, import_path: str
) -> str | None:
    if import_path == module_path:
        return module_folder
    if not import_path.startswith(f"{module_path}/"):
        return None
    relative = import_path.removeprefix(f"{module_path}/")
    return PurePosixPath(module_folder, relative).as_posix()
