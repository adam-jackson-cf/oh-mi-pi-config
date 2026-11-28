from __future__ import annotations

import importlib.util
import json
import posixpath
import re
import tomllib
from collections import defaultdict
from collections.abc import Callable, Container, Iterable
from dataclasses import dataclass, field
from os.path import commonprefix
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.foundation.source.python import (
    IMPORT_KINDS,
    PythonImport,
    imports,
    literal_value,
    text_literal,
)
from guardrails.foundation.source.tree import (
    SourceError,
    language_of,
    language_pack,
    languages,
    of_kinds,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

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
PACKAGE_MANIFEST = "package.json"
DOTTED_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
CHAIN_KINDS = frozenset({"attribute"})
NAME_KINDS = frozenset({"identifier"})
TEXT_KINDS = frozenset({"string", "concatenated_string"})
# Identifiers that bind a name rather than read one: parameters and global or nonlocal
# declarations, and the name field of a definition, a defaulted parameter or a keyword.
BINDING_PARENTS = frozenset({
    "parameters",
    "lambda_parameters",
    "list_splat_pattern",
    "dictionary_splat_pattern",
    "global_statement",
    "nonlocal_statement",
})
NAMED_PARENTS = frozenset({
    "function_definition",
    "class_definition",
    "default_parameter",
    "typed_default_parameter",
    "keyword_argument",
})


@dataclass(frozen=True)
class SymbolUse:
    """A reference to the top-level name symbol of the file at path, at a byte offset of
    the referring file (-1 for an import statement)."""

    path: str
    symbol: str
    at: int


@dataclass(frozen=True)
class Reexport:
    """A name a module binds from another file, which the module's importers can read
    through it: local (None for every name, as a star import or export * gives) stands
    for the target file's name imported (None: the same name)."""

    local: str | None
    target: str
    imported: str | None


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

    def symbol(
        self, dotted: str, importer: str, *, relative: bool
    ) -> tuple[str, str] | None:
        """The file and top-level name a dotted name reaches from an importer: the longest
        prefix that names a module file, then the name after it."""
        parts = dotted.split(".")
        for size in range(len(parts) - 1, 0, -1):
            found = self.lookup(".".join(parts[:size]), importer, relative=relative)
            if found:
                return found[0], parts[size]
        return None

    def in_package(self, path: str, package: str) -> bool:
        """Whether a file imports as the package or a module inside it."""
        return any(
            name == package or name.startswith(f"{package}.")
            for name in self.names.get(path, [])
        )

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


@dataclass(frozen=True)
class PythonReferences:
    uses: tuple[SymbolUse, ...]
    reexports: tuple[Reexport, ...]


Binding = tuple[str, bool]


def import_bindings(
    path: str, items: list[PythonImport], index: PythonIndex
) -> tuple[dict[str, Binding], list[SymbolUse], list[Reexport]]:
    """Each name a file's imports bind, as the absolute dotted name it stands for and
    whether it resolves from the repository root (a relative import); each from-import's
    use of the name it takes, and the re-exports those names give the module."""
    module = python_module(path)
    bound: dict[str, Binding] = {}
    uses: list[SymbolUse] = []
    reexports: list[Reexport] = []
    for item in items:
        if item.statement == "import":
            name = item.bindings[0]
            bound[name] = (
                name if name == item.module.partition(".")[0] else item.module,
                False,
            )
            continue
        try:
            base = absolute(item, module)
        except SourceError:
            continue
        relative = bool(item.level)
        for name, local in zip(item.names, item.bindings, strict=True):
            if name == "*":
                reexports.extend(
                    Reexport(None, target, None)
                    for target in index.lookup(base, path, relative=relative)
                )
                continue
            dotted = f"{base}.{name}" if base else name
            bound[local] = (dotted, relative)
            found = index.symbol(dotted, path, relative=relative)
            if found is not None:
                uses.append(SymbolUse(found[0], found[1], -1))
                reexports.append(Reexport(local, found[0], found[1]))
    return bound, uses, reexports


def binds_name(node: SgNode) -> bool:
    parent = node.parent()
    if parent is None:
        return False
    if parent.kind() in BINDING_PARENTS:
        return True
    if parent.kind() == "typed_parameter":
        first = next(iter(parent.children()), None)
        return (
            first is not None and first.range().start.index == node.range().start.index
        )
    own = parent.field("name") if parent.kind() in NAMED_PARENTS else None
    return own is not None and own.range().start.index == node.range().start.index


def outermost_chain(node: SgNode) -> bool:
    parent = node.parent()
    inner = (
        parent.field("object")
        if parent is not None and parent.kind() in CHAIN_KINDS
        else None
    )
    return inner is None or inner.range().start.index != node.range().start.index


def read_names(root: SgNode) -> list[tuple[str, int, bool]]:
    """The dotted names a file reads, with the offset of each: the outermost attribute
    chain of plain names, each other name read outside import statements, and each
    string literal that holds a dotted name (as mock.patch targets and __all__ do)."""
    consumed: set[int] = set()
    for statement in of_kinds(root, IMPORT_KINDS):
        consumed.update(
            node.range().start.index for node in of_kinds(statement, NAME_KINDS)
        )
    found: list[tuple[str, int, bool]] = []
    for chain in of_kinds(root, CHAIN_KINDS):
        text = "".join(chain.text().split())
        if not outermost_chain(chain) or not DOTTED_NAME.fullmatch(text):
            continue
        consumed.update(
            node.range().start.index for node in of_kinds(chain, NAME_KINDS)
        )
        found.append((text, chain.range().start.index, False))
    for node in of_kinds(root, NAME_KINDS):
        start = node.range().start.index
        if start not in consumed and not binds_name(node):
            found.append((node.text(), start, False))
    for node in of_kinds(root, TEXT_KINDS):
        parent = node.parent()
        if parent is not None and parent.kind() == "concatenated_string":
            continue
        try:
            value = literal_value(node.text()) if text_literal(node) else None
        except SourceError:
            continue
        if isinstance(value, str) and DOTTED_NAME.fullmatch(value):
            found.append((value, node.range().start.index, True))
    return found


def named_modules(
    path: str, names: list[tuple[str, int, bool]], index: PythonIndex
) -> list[str]:
    """The module files a file names by a string holding a whole dotted module name, as
    importlib dispatch tables do."""
    found: list[str] = []
    for text, _, literal in names:
        if literal and "." in text:
            found.extend(index.lookup(text, path, relative=False))
    return sorted(set(found) - {path})


def python_references(path: str, root: SgNode, index: PythonIndex) -> PythonReferences:
    """The top-level names of the repository's files a Python file reads. A name an import
    binds resolves through the import (a relative one from the file's package), and so
    does a module attribute read through it (shop.core.reset); any other name is the
    file's own. A from-import itself reads the name it takes, and re-exports it. A file
    that names modules by string (importlib dispatch) reads from them each attribute it
    reads on a name it cannot resolve (module.run) and each name it holds as a string
    (getattr(module, "run"))."""
    bound, uses, reexports = import_bindings(path, imports(root), index)
    names = read_names(root)
    loaded = named_modules(path, names, index)
    for text, at, literal in names:
        head, _, rest = text.partition(".")
        if literal and rest:
            found = index.symbol(text, path, relative=False)
        elif head in bound:
            dotted, relative = bound[head]
            name = f"{dotted}.{rest}" if rest else dotted
            found = index.symbol(name, path, relative=relative)
        else:
            found = (path, head)
            dynamic = head if literal else rest.partition(".")[0]
            uses.extend(SymbolUse(module, dynamic, at) for module in loaded if dynamic)
        if found is not None:
            uses.append(SymbolUse(found[0], found[1], at))
    return PythonReferences(tuple(uses), tuple(reexports))


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


def package_manifest(path: str, text: str) -> dict[str, Any]:
    """A package.json file's object; SourceError when it is not one."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise SourceError(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise SourceError(f"{path} must hold a JSON object")
    return data


def package_entry(manifest: dict[str, Any]) -> str | None:
    """The file a bare import of a package names, relative to its folder: package.json
    exports when it is a string or has a string "." entry, or else main."""
    exports = manifest.get("exports")
    if isinstance(exports, dict):
        exports = exports.get(".")
    return next(
        (value for value in (exports, manifest.get("main")) if isinstance(value, str)),
        None,
    )


def pyproject(path: str, text: str) -> dict[str, Any]:
    """A pyproject.toml file's tables; SourceError when it is not valid TOML."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SourceError(f"{path}: {error}") from error


def project_scripts(path: str, text: str) -> list[tuple[str, str]]:
    """The scripts a pyproject.toml declares ([project.scripts], [project.gui-scripts],
    [tool.poetry.scripts]), each name with its "module:function" target; SourceError when
    the file is not TOML."""
    data = pyproject(path, text)
    project = data.get("project", {})
    poetry = data.get("tool", {}).get("poetry", {})
    found = []
    for section in (
        project.get("scripts", {}),
        project.get("gui-scripts", {}),
        poetry.get("scripts", {}),
    ):
        for key, value in (section or {}).items():
            target = (
                value.get("reference", value.get("callable", ""))
                if isinstance(value, dict)
                else value
            )
            if isinstance(target, str):
                found.append((key, target))
    return found


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
