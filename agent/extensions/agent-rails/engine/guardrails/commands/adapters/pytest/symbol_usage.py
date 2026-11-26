"""The pytest adapter's part of the shared test-only tally: Python definitions and the
uses modules.python_references resolves through imports and module attributes."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from guardrails.commands.adapters.owned_source import READ_ERRORS, manifest_finding
from guardrails.commands.adapters.symbol_tally import Defined, Key, Tally
from guardrails.context.config.entry_points import EntryPoint
from guardrails.foundation.findings import Finding
from guardrails.foundation.source.modules import (
    DOTTED_NAME,
    PythonIndex,
    SymbolUse,
    project_scripts,
    python_references,
)
from guardrails.foundation.source.python import defined
from guardrails.foundation.source.tree import line, named, read

if TYPE_CHECKING:
    from ast_grep_py import SgNode

    from guardrails.commands.adapters.owned_source import Source

DEFINITIONS = frozenset({"function_definition", "class_definition"})


@dataclass(frozen=True)
class Owner:
    """The Python files of a repository by import name, and the owned packages among the
    entry points: dotted package names, not the folder paths other languages own."""

    index: PythonIndex
    packages: frozenset[str]

    def owns(self, path: str) -> bool:
        return any(self.index.in_package(path, package) for package in self.packages)


def owner(points: Iterable[EntryPoint], files: Iterable[str]) -> Owner:
    packages = frozenset(
        point.package for point in points if DOTTED_NAME.fullmatch(point.package)
    )
    return Owner(PythonIndex(files), packages)


PRIVATE_PREFIX = "_"


def top_level(root: SgNode) -> list[tuple[SgNode, SgNode, str]]:
    """Each public top-level function and class: its statement, definition and name."""
    found = []
    for statement in named(root):
        node = defined(statement)
        name = node.field("name")
        if (
            node.kind() in DEFINITIONS
            and name is not None
            and not name.text().startswith(PRIVATE_PREFIX)
        ):
            found.append((statement, node, name.text()))
    return found


def definitions(production: Mapping[str, Source]) -> dict[Key, Defined]:
    found: dict[Key, Defined] = {}
    for path, source in production.items():
        for statement, node, name in top_level(source.root):
            span = (statement.range().start.index, statement.range().end.index)
            found[path, name] = Defined(line(node), span)
    return found


def public_entries(points: Iterable[EntryPoint], index: PythonIndex) -> list[Key]:
    return [
        found
        for point in points
        if (found := index.symbol(f"{point.module}.{point.symbol}", "", relative=False))
    ]


@dataclass(frozen=True)
class ScriptManifests:
    """The pyproject.toml files whose declared scripts are production uses, and the
    config's entry points: what reaches production code from outside its source."""

    repo: Path
    manifests: list[str]
    points: Iterable[EntryPoint]


def script_uses(
    found: ScriptManifests, index: PythonIndex
) -> tuple[list[tuple[str, SymbolUse]], list[Finding]]:
    """Each declared script's function, as a use by its manifest, and a finding for each
    manifest that does not read."""
    uses: list[tuple[str, SymbolUse]] = []
    problems: list[Finding] = []
    for manifest in found.manifests:
        try:
            scripts = project_scripts(manifest, read(found.repo, manifest))
        except READ_ERRORS as error:
            problems.append(manifest_finding(manifest, error))
            continue
        for _, target in scripts:
            module, _, function = target.partition(":")
            name = f"{module.strip()}.{function.strip().partition('.')[0]}"
            symbol = index.symbol(name, manifest, relative=False)
            if symbol is not None:
                uses.append((manifest, SymbolUse(symbol[0], symbol[1], -1)))
    return uses, problems


def test_only_findings(
    sources: Mapping[str, Source],
    is_test: Callable[[str], bool],
    reported: Collection[str],
    index: PythonIndex,
    outside: ScriptManifests,
) -> list[Finding]:
    production = {path: source for path, source in sources.items() if not is_test(path)}
    tally = Tally(definitions(production))
    references = {
        path: python_references(path, source.root, index)
        for path, source in sources.items()
    }
    tally.reexports.update(
        (path, list(found.reexports)) for path, found in references.items()
    )
    for path in sorted(references):
        tally.record(path, references[path].uses, test=is_test(path))
    scripts, problems = script_uses(outside, index)
    for manifest, use in scripts:
        tally.record(manifest, [use], test=False)
    public = tally.public(public_entries(outside.points, index))
    return [*problems, *tally.findings(reported, public)]
