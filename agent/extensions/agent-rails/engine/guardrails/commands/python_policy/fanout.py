from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.checker.python_rules.settings import (
    PythonSettings,
    finding,
    inventory_finding,
    load_settings,
    matched_root,
    parse_paths,
    python_files,
    segments,
    setting_finding,
)
from guardrails.context.config import house_style
from guardrails.context.config.entry_points import packages as entry_point_packages
from guardrails.context.config.section_schema import ConfigValueError
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import GLOB_CHARACTERS, InventoryError, as_list
from guardrails.foundation.source import modules, python, tree

if TYPE_CHECKING:
    from ast_grep_py import SgNode

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
    module: modules.PythonModule | None


def module_for(relpath: str, roots: list[str]) -> Module:
    depth = matched_root(relpath, roots)
    if depth is None:
        return Module(relpath, None)
    return Module(relpath, modules.python_module("/".join(segments(relpath)[depth:])))


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

    def first_party(self, module: str) -> bool:
        return module.split(".", 1)[0] in self.root_packages

    def import_modules(
        self, item: python.PythonImport, importer: modules.PythonModule
    ) -> set[str]:
        target = modules.absolute(item, importer)
        if item.statement == "import" or not self.first_party(target):
            return {self.normalise(target)}
        return {self.normalise(name) for name in modules.imported_names(item, importer)}

    def imports(self, root: SgNode, current: Module) -> set[str]:
        """The architectural modules a file imports; SourceError for a relative import
        that does not resolve."""
        importer = current.module or modules.PythonModule("", is_package=True)
        statements = [
            *python.imports(root),
            *(item for _, item in python.dynamic_imports(root)),
        ]
        found: set[str] = set()
        for item in statements:
            found.update(self.import_modules(item, importer))
        found.discard(importer.name)
        return {module for module in found if not is_stdlib(module)}


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
    return set(entry_point_packages(config))


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
    try:
        packages = root_packages(config, options)
    except ConfigValueError as error:
        return [
            *found,
            setting_finding(GENERAL, "fanout.root_packages", f"is unset and {error}"),
        ]
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
    found_modules = [
        module_for(path, roots)
        for path in everything
        if matched_root(path, roots) is not None
    ]
    collector = ImportCollector(
        root_packages(config, options),
        {m.module.name for m in found_modules if m.module and m.module.name},
    )
    chosen = set(python_files(repo, options, selected)) if selected else None
    limit = limits(options)
    found: list[Finding] = []
    for module in found_modules:
        if chosen is not None and module.relpath not in chosen:
            continue
        try:
            text = tree.read(repo, module.relpath)
            imports = collector.imports(tree.parse(module.relpath, text), module)
        except tree.SourceError as error:
            found.append(
                finding(
                    GENERAL,
                    module.relpath,
                    None,
                    str(error),
                    "restore readable UTF-8 Python with imports that resolve",
                )
            )
            continue
        found.extend(module_findings(module, imports, source_nloc(text), limit))
    return house_style.apply_exceptions(found, config)
