from __future__ import annotations

import importlib.util
import json
import posixpath
import re
from collections import defaultdict
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass, field
from os.path import commonprefix
from pathlib import PurePosixPath
from typing import Any

from guardrails.foundation.source.python import PythonImport
from guardrails.foundation.source.tree import (
    SourceError,
    language_of,
    language_pack,
    languages,
)

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
TSCONFIG = "tsconfig.json"


@dataclass(frozen=True)
class PythonModule:
    """A Python module's dotted name, and whether its file is a package's marker."""

    name: str
    is_package: bool

    @property
    def package(self) -> str:
        """The package a relative import in this module resolves against."""
        return self.name if self.is_package else self.name.rpartition(".")[0]


def package_marker() -> str:
    marker: str = language_pack("python")["package_marker"]
    return marker


def stub_extension() -> str:
    extension: str = language_pack("python")["stub_extension"]
    return extension


def python_module(relative: str, package: str = "") -> PythonModule:
    """The module a Python file imports as, from its path relative to an import root (or
    to the folder of package): a.py and its a.pyi stub are module a, and pkg/__init__.py
    and pkg/__init__.pyi are the package pkg itself."""
    path = PurePosixPath(relative)
    is_package = path.stem == PurePosixPath(package_marker()).stem
    parts = [*([package] if package else []), *path.parent.parts]
    return PythonModule(
        ".".join(parts if is_package else [*parts, path.stem]), is_package
    )


def absolute(item: PythonImport, importer: PythonModule) -> str:
    """The absolute module an import statement names: a relative import resolves against
    the importer's package as Python's import system does, and one that climbs above the
    top-level package, or sits in a module with no package, is a SourceError."""
    if not item.level:
        return item.module
    try:
        return importlib.util.resolve_name(
            "." * item.level + item.module, importer.package
        )
    except ImportError as error:
        raise SourceError(f"line {item.line}: {error}", item.line) from error


def imported_names(item: PythonImport, importer: PythonModule) -> list[str]:
    """The dotted names an import binds: the module of an import statement, and each
    module.name of a from-import (the module itself for a wildcard). A name is a
    submodule or an attribute of the module; resolvers find the module that holds it."""
    target = absolute(item, importer)
    if item.statement == "import":
        return [target]
    return [target if name == "*" else f"{target}.{name}" for name in item.names]


def python_edges(
    imports: list[PythonImport], module: PythonModule, package: str
) -> list[str]:
    edges = [edge for item in imports for edge in imported_names(item, module)]
    return [edge for edge in edges if edge == package or edge.startswith(f"{package}.")]


def shared_folders(first: str, second: str) -> int:
    return len(
        commonprefix([
            PurePosixPath(first).parent.parts,
            PurePosixPath(second).parent.parts,
        ])
    )


class PythonIndex:
    """The Python files of a tree under every name Python could import them by when no
    sys.path is configured. Any folder may be a sys.path entry, so a file is named by
    every dotted suffix of its path (src/shop/cart.py is src.shop.cart, shop.cart and
    cart). A single name is a top-level module: it names a file in the importer's own
    folder (a script's directory) or one whose folder is not a regular package, so a
    module inside a package does not answer for a standard-library name. A .pyi stub
    shares its module's name and is the target only when no .py file has that name."""

    def __init__(self, paths: Iterable[str]) -> None:
        files = sorted(path for path in paths if language_of(path) == "python")
        marker = package_marker()
        packages = {
            folder_of(path) for path in files if PurePosixPath(path).name == marker
        }
        self.named: dict[str, list[str]] = defaultdict(list)
        self.local: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.full: dict[str, list[str]] = defaultdict(list)
        self.names: dict[str, list[str]] = {}
        for path in files:
            module = python_module(path)
            parts = module.name.split(".") if module.name else []
            home = folder_of(folder_of(path)) if module.is_package else folder_of(path)
            top_level = home == "." or home not in packages
            names = [".".join(parts[start:]) for start in range(len(parts))]
            if names:
                self.full[names[0]].append(path)
                self.local[folder_of(path), names[-1]].append(path)
            if names and not top_level:
                names.pop()
            for name in names:
                self.named[name].append(path)
            self.names[path] = names

    def files(self, found: list[str], importer: str) -> list[str]:
        sources = [
            path for path in dict.fromkeys(found) if not path.endswith(stub_extension())
        ] or list(dict.fromkeys(found))
        if len(sources) <= 1:
            return sources
        return [
            max(
                sources,
                key=lambda path: (
                    shared_folders(path, importer),
                    len(commonprefix([path, importer])),
                    path,
                ),
            )
        ]

    def lookup(self, name: str, importer: str, *, relative: bool) -> list[str]:
        if relative:
            return self.files(self.full.get(name, []), importer)
        found = [*self.named.get(name, [])]
        if "." not in name:
            found.extend(self.local.get((folder_of(importer), name), []))
        return self.files(found, importer)

    def resolve(self, importer: str, item: PythonImport) -> list[str]:
        """The files an import in importer names, one per name as Python binds a name
        to one module: the nearest file (most shared folders, then the longest shared
        path), for the imported module, and for a from-import each name as a submodule
        or else the module that holds it. A relative import resolves against
        the importer's folders from the repository root, so one that climbs above it
        names no file."""
        module = python_module(importer)
        try:
            names = imported_names(item, module)
        except SourceError:
            return []
        relative = bool(item.level)
        found: set[str] = set()
        for name in names:
            options = (
                [name]
                if item.statement == "import" or "*" in item.names
                else [name, name.rpartition(".")[0]]
            )
            found.update(
                next(
                    (
                        hits
                        for option in options
                        if option
                        and (hits := self.lookup(option, importer, relative=relative))
                    ),
                    [],
                )
            )
        found.discard(importer)
        return sorted(found)


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


@dataclass
class ScriptResolver:
    """Resolves TypeScript and JavaScript import specifiers to tracked files.

    Each importer reads its nearest tsconfig.json, parsed once per file.
    """

    tracked: Container[str]
    read: Callable[[str], str]
    configs: dict[str, TsConfig] = field(default_factory=dict)

    def config(self, path: str) -> TsConfig:
        if path not in self.configs:
            self.configs[path] = tsconfig(path, self.read(path))
        return self.configs[path]

    def config_path(self, importer: str) -> str | None:
        return nearest(importer, TSCONFIG, self.tracked)

    def importer_config(self, importer: str) -> TsConfig | None:
        found = self.config_path(importer)
        return None if found is None else self.config(found)

    def resolve(self, importer: str, specifier: str) -> str | None:
        config = self.importer_config(importer)
        return resolve_specifier(importer, specifier, self.tracked, config)


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
