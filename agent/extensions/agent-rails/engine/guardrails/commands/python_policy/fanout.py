from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.checker.python_rules.settings import (
    PythonSettings,
    finding,
    inventory_finding,
    load_settings,
    matched_root,
    parse_paths,
    python_files,
    read_source,
    segments,
    setting_finding,
)
from guardrails.context.config import house_style
from guardrails.context.config.reader import tests_settings
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import GLOB_CHARACTERS, InventoryError, as_list

GENERAL = "FANOUT.general"
LARGE = "FANOUT.large-module"
LIMIT_DEFAULTS = {
    "architectural_max": 10,
    "large_module_nloc": 800,
    "large_module_architectural_max": 3,
}
FIX = "split the module by responsibility or route imports through a narrower owner"


@dataclass(frozen=True)
class Module:
    relpath: str
    name: str | None
    is_package: bool


def module_for(relpath: str, roots: list[str]) -> Module:
    depth = matched_root(relpath, roots)
    if depth is None:
        return Module(relpath, None, False)
    parts = segments(relpath)[depth:]
    if parts[-1] == "__init__.py":
        return Module(relpath, ".".join(parts[:-1]) or None, True)
    return Module(
        relpath, ".".join((*parts[:-1], PurePosixPath(parts[-1]).stem)), False
    )


def resolve_relative(current: Module, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    if current.name is None:
        return None
    package = (
        current.name.split(".") if current.is_package else current.name.split(".")[:-1]
    )
    if node.level > 1:
        package = package[: -(node.level - 1)]
    if node.module:
        package.extend(node.module.split("."))
    return ".".join(package)


class ImportCollector:
    def __init__(self, root_packages: set[str], known: set[str]) -> None:
        self.root_packages = root_packages
        self.known = known | root_packages

    def normalise(self, module: str) -> str:
        root = module.split(".", 1)[0]
        if root not in self.root_packages:
            return module
        parts = module.split(".")
        for end in range(len(parts), 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in self.known:
                return candidate
        return root

    def from_import(self, node: ast.ImportFrom, current: Module) -> set[str]:
        module = resolve_relative(current, node)
        if module is None:
            return set()
        if module.split(".", 1)[0] not in self.root_packages:
            return {module}
        found = set()
        for alias in node.names:
            candidate = module if alias.name == "*" else f"{module}.{alias.name}"
            found.add(self.normalise(candidate if candidate in self.known else module))
        return found

    def imports(self, tree: ast.Module, current: Module) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(tree):
            literal = literal_import_module(node)
            if literal is not None:
                found.add(self.normalise(literal))
            elif isinstance(node, ast.Import):
                found.update(self.normalise(alias.name) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                found.update(self.from_import(node, current))
        found.discard(current.name or "")
        return {module for module in found if not is_stdlib(module)}


def literal_import_module(node: ast.AST) -> str | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.args
    ):
        return None
    first = node.args[0]
    return (
        first.value
        if isinstance(first, ast.Constant) and isinstance(first.value, str)
        else None
    )


def is_stdlib(module: str) -> bool:
    root = module.split(".", 1)[0]
    return root == "__future__" or root in sys.stdlib_module_names


def source_nloc(text: str) -> int:
    return sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def root_packages(config: dict[str, Any], options: PythonSettings) -> set[str]:
    section = options.mapping("fanout")
    if "root_packages" in section:
        return set(as_list(section["root_packages"]))
    entry_points = tests_settings(config, "public_entry_points")
    return set(entry_points) if isinstance(entry_points, dict) else set()


def limits(options: PythonSettings) -> dict[str, Any]:
    return {
        key: options.number("fanout", key, value)
        for key, value in LIMIT_DEFAULTS.items()
    }


def setting_findings(
    repo: Path, config: dict[str, Any], options: PythonSettings
) -> list[Finding]:
    roots = options.paths("source_roots")
    if not roots:
        return [setting_finding(GENERAL, "source_roots", "is not configured")]
    found = [
        setting_finding(
            GENERAL, "source_roots", f"names '{root}', which is not a directory"
        )
        for root in roots
        if not GLOB_CHARACTERS & set(root) and not (repo / root).is_dir()
    ]
    packages = root_packages(config, options)
    if not packages or any(not package.isidentifier() for package in packages):
        found.append(
            setting_finding(
                GENERAL,
                "fanout.root_packages",
                "must name first-party packages (default: public_entry_points packages)",
            )
        )
    return found


def module_findings(
    module: Module, imports: set[str], nloc: int, limit: dict[str, Any]
) -> list[Finding]:
    listed = ", ".join(sorted(imports))
    found = []
    if len(imports) > limit["architectural_max"]:
        found.append(
            finding(
                GENERAL,
                module.relpath,
                None,
                f"architectural imports {len(imports)} > {limit['architectural_max']} ({listed})",
                FIX,
            )
        )
    if (
        nloc > limit["large_module_nloc"]
        and len(imports) > limit["large_module_architectural_max"]
    ):
        found.append(
            finding(
                LARGE,
                module.relpath,
                None,
                f"large module {nloc} NLOC has architectural imports {len(imports)} > "
                f"{limit['large_module_architectural_max']} ({listed})",
                FIX,
            )
        )
    return found


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    selected = parse_paths("python-fanout", argv)
    options = load_settings(config)
    problems = setting_findings(repo, config, options)
    if problems:
        return problems
    roots = options.paths("source_roots")
    try:
        everything = python_files(repo, options, [])
    except InventoryError as error:
        return [inventory_finding(GENERAL, error)]
    modules = [
        module_for(path, roots)
        for path in everything
        if matched_root(path, roots) is not None
    ]
    collector = ImportCollector(
        root_packages(config, options), {m.name for m in modules if m.name}
    )
    chosen = set(python_files(repo, options, selected)) if selected else None
    limit = limits(options)
    found: list[Finding] = []
    for module in modules:
        if chosen is not None and module.relpath not in chosen:
            continue
        source = read_source(repo, module.relpath)
        if source.tree is None:
            found.append(
                finding(
                    GENERAL,
                    module.relpath,
                    None,
                    source.error,
                    "restore readable UTF-8 Python",
                )
            )
            continue
        imports = collector.imports(source.tree, module)
        found.extend(module_findings(module, imports, source_nloc(source.text), limit))
    return house_style.apply_exceptions(found, config)
