"""The Go adapter's part of the shared test-only tally: package-level functions and types,
and the uses foundation/source/go.references reads, resolved through package import paths
(pkg.Name) and the file's own package (Name)."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from typing import TYPE_CHECKING

from guardrails.commands.adapters.go.ownership import GoModules
from guardrails.commands.adapters.symbol_tally import Defined, Key, Tally
from guardrails.context.config.entry_points import EntryPoint
from guardrails.foundation.findings import Finding
from guardrails.foundation.source import go
from guardrails.foundation.source.modules import SymbolUse, folder_of
from guardrails.foundation.source.tree import line, named

if TYPE_CHECKING:
    from ast_grep_py import SgNode

ENTRY_FUNCTIONS = frozenset({"main", "init"})
# Each package-level name of a package folder: the file that defines it and its package.
Packages = dict[tuple[str, str], tuple[str, str]]


def declared(root: SgNode) -> list[tuple[SgNode, SgNode]]:
    """Each package-level function (not main or init) and type: its node and name."""
    found = []
    for node in named(root):
        name = node.field("name")
        if (
            node.kind() == "function_declaration"
            and name is not None
            and name.text() not in ENTRY_FUNCTIONS
        ):
            found.append((node, name))
        if node.kind() == "type_declaration":
            found.extend(
                (spec, spec_name)
                for spec in named(node)
                if spec.kind() == "type_spec" and (spec_name := spec.field("name"))
            )
    return found


def definitions(
    production: Mapping[str, SgNode],
) -> tuple[dict[Key, Defined], Packages]:
    found: dict[Key, Defined] = {}
    packages: Packages = {}
    for path, root in production.items():
        package = go.package_name(root)
        for node, name in declared(root):
            span = (node.range().start.index, node.range().end.index)
            found[path, name.text()] = Defined(line(name), span)
            packages.setdefault((folder_of(path), name.text()), (path, package))
    return found, packages


def file_uses(
    path: str, root: SgNode, packages: Packages, modules: GoModules
) -> list[SymbolUse]:
    own = go.package_name(root)
    uses = []
    for reference in go.references(root):
        if reference.import_path is None:
            target = packages.get((folder_of(path), reference.name))
            if target is None or target[1] != own:
                continue
        else:
            folder = modules.folder(path, reference.import_path)
            target = None if folder is None else packages.get((folder, reference.name))
            if target is None:
                continue
        uses.append(SymbolUse(target[0], reference.name, reference.at))
    return uses


def public_entries(
    points: Iterable[EntryPoint], packages: Packages, modules: GoModules
) -> list[Key]:
    found = []
    for point in points:
        folder = modules.entry_folder(point.module)
        target = None if folder is None else packages.get((folder, point.symbol))
        if target is not None:
            found.append((target[0], point.symbol))
    return found


def test_only_findings(
    sources: Mapping[str, SgNode],
    is_test: Callable[[str], bool],
    reported: Collection[str],
    modules: GoModules,
    points: Iterable[EntryPoint],
) -> list[Finding]:
    production = {path: root for path, root in sources.items() if not is_test(path)}
    found, packages = definitions(production)
    tally = Tally(found)
    for path in sorted(sources):
        uses = file_uses(path, sources[path], packages, modules)
        tally.record(path, uses, test=is_test(path))
    public = tally.public(public_entries(points, packages, modules))
    return tally.findings(reported, public)
