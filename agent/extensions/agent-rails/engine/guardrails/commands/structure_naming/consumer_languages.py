"""The consumer proposer's facts for TypeScript and Go files: the program, package, import
and launch signals structure-languages.json proposers.signals names for them, and the
path and root-folder tests every language shares."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.commands.structure_naming import importers, programs
from guardrails.commands.structure_naming.outline import first_line, go_outline
from guardrails.commands.structure_naming.scan_scope import is_vendored_path, runs
from guardrails.context.config.suite_files import is_test_source
from guardrails.foundation.source import go, modules, typescript
from guardrails.foundation.source.tree import SourceError, language_of, of_kinds, parse

if TYPE_CHECKING:
    from guardrails.foundation.source.tree import SgNode

ROOT_NON_PACKAGE = frozenset({"fixtures", "migrations", "scripts", "tests"})
SKILL_MANIFEST = "SKILL.md"
SCRIPT_STRINGS = frozenset({"string", "template_string"})
NODE_SCHEME = "node:"
MAIN_PACKAGE = "main"
MAIN_FUNCTION = "main"
MAIN_MARKER = "main_package"
Facts = dict[str, Any]


def parent(path: str) -> str:
    folder = PurePosixPath(path).parent.as_posix()
    return "" if folder == "." else folder


def join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def excluded(path: str, tree: Collection[str]) -> bool:
    """A vendored or hidden path, or one at or below a skill bundle's folder."""
    if is_vendored_path(path):
        return True
    folder = parent(path)
    while True:
        if join(folder, SKILL_MANIFEST) in tree:
            return True
        if not folder:
            return False
        folder = parent(folder)


def in_root_folder(path: str) -> bool:
    """A file under a root folder of its own (fixtures, migrations, scripts, tests)."""
    return path.split("/")[0] in ROOT_NON_PACKAGE


def names_file(
    literal: str, path: str, dotted: Collection[str], top: str, source: str
) -> bool:
    """Whether a string literal in source names the file at path: by a dotted module
    name, by its path (a leading ./ dropped), or inside the top folder by its name."""
    if literal in dotted:
        return True
    text = literal.removeprefix("./")
    if "/" in text and (path == text or path.endswith("/" + text)):
        return True
    return text == PurePosixPath(path).name and (
        source == top or source.startswith(top + "/")
    )


def parsed(path: str, source: str) -> SgNode | None:
    """A file's tree; an unparseable file, or a Go file without a package clause (the go
    tool rejects it), is skipped (None): it is no candidate and imports nothing."""
    try:
        root = parse(path, source)
        if language_of(path) == "go":
            go.package_clause(root)
    except SourceError:
        return None
    return root


def literals_of(root: SgNode, language: str) -> list[str]:
    """The string literals of a file that name a source file of its language by path."""
    if language == "typescript":
        values = [typescript.literal(node) for node in of_kinds(root, SCRIPT_STRINGS)]
    else:
        values = [go.string_value(node) for node in of_kinds(root, go.STRING_KINDS)]
    return [
        value.strip()
        for value in values
        if value is not None and runs(value.strip().removeprefix("./"), (language,))
    ]


class Launches:
    """Which files of a tree are referenced by path: by a string literal in another
    non-test file, or (TypeScript) by a package.json scripts command or an HTML page."""

    def __init__(
        self,
        tree: dict[str, str],
        roots: dict[str, SgNode],
        language: str,
        config: dict[str, Any],
    ) -> None:
        self.literals = {
            path: found
            for path, root in roots.items()
            if not is_test_source(path, config)
            and (found := literals_of(root, language))
        }
        self.launched: set[str] = set()
        if language != "typescript":
            return
        tracked = set(tree)
        for path in sorted(tree):
            if PurePosixPath(path).name == programs.package_file(language):
                self.launched |= programs.launched_scripts(path, tree[path], tracked)
            elif programs.is_page(path):
                self.launched |= programs.page_scripts(path, tree[path], tracked)

    def referenced(self, path: str, top: str) -> bool:
        return path in self.launched or any(
            names_file(literal, path, (), top, source)
            for source, literals in self.literals.items()
            if source != path
            for literal in literals
        )


def facts(**found: Any) -> Facts:
    """A candidate's facts in the shape every language's proposer score reads."""
    return {
        "package_init": None,
        "dunder_main": False,
        **found,
        "ui_app_marker": any(
            marker in programs.ui_app_markers(found["language"])
            for marker in found["run_markers"]
        ),
    }


def package_of(specifier: str) -> str:
    """The npm package a bare specifier names (a scope's first two parts)."""
    parts = specifier.removeprefix(NODE_SCHEME).split("/")
    return "/".join(parts[:2]) if parts[0].startswith("@") else parts[0]


def inside(path: str, folder: str) -> bool:
    return folder == "." or path.startswith(folder + "/")


def script_facts(
    tree: dict[str, str], roots: dict[str, SgNode], config: dict[str, Any]
) -> list[tuple[str, Facts]]:
    language = "typescript"
    resolver = modules.ScriptResolver(set(tree), tree.__getitem__)
    graph = importers.script_importers(tree, roots)
    launches = Launches(tree, roots, language, config)
    declared: set[str] = set()
    for path in sorted(tree):
        if PurePosixPath(path).name == programs.package_file(language):
            declared |= programs.declared_scripts(path, tree[path], set(tree))
    libraries = programs.ui_libraries(language)
    found = []
    for path in sorted(roots):
        manifest = modules.nearest(path, programs.package_file(language), tree)
        if manifest is None or excluded(path, tree) or in_root_folder(path):
            continue
        package = modules.folder_of(manifest)
        data = programs.manifest_data(manifest, tree[manifest])
        texts, own, ui = [], 0, set()
        for item in typescript.imports(roots[path]):
            texts.append(importers.line_text(tree[path], item.line))
            target = resolver.resolve(path, item.specifier)
            if target is not None:
                own += inside(target, package)
            elif package_of(item.specifier) in libraries:
                ui.add(package_of(item.specifier))
        found.append((
            path,
            facts(
                language=language,
                package_dir=package,
                package_name=data.get("name")
                if isinstance(data.get("name"), str)
                else None,
                description=first_line(str(data.get("description") or "")),
                run_markers=programs.run_markers(language, tree[path]),
                imported_by=len(graph.get(path, [])),
                own_package_imports=own,
                ui_libs=sorted(ui),
                in_project_scripts=path in declared,
                test_path=is_test_source(path, config),
                imports=texts,
                referenced_by_path=launches.referenced(path, package),
            ),
        ))
    return found


def main_program(path: str, root: SgNode, source: str) -> bool:
    """A built non-test file of package main that declares func main."""
    return (
        go.built(path, root)
        and not go.is_test_file(path)
        and go.package_name(root) == MAIN_PACKAGE
        and any(item.name == MAIN_FUNCTION for item in go.definitions(root, source))
    )


def module_doc(module: str, tree: dict[str, str], roots: dict[str, SgNode]) -> str:
    """The first line of the package doc of the module root's package."""
    for path in sorted(roots):
        if modules.folder_of(path) == module and go.built(path, roots[path]):
            doc = first_line(go_outline(roots[path], tree[path]).doc)
            if doc:
                return doc
    return ""


def go_facts(
    tree: dict[str, str], roots: dict[str, SgNode], config: dict[str, Any]
) -> list[tuple[str, Facts]]:
    language = "go"
    graph = importers.go_importers(tree, roots)
    owners = importers.GoModules(tree, {})
    launches = Launches(tree, roots, language, config)
    libraries = programs.ui_libraries(language)
    found = []
    for path in sorted(roots):
        root = roots[path]
        module = owners.module(path)
        if (
            module is None
            or not main_program(path, root, tree[path])
            or excluded(path, tree)
            or in_root_folder(path)
        ):
            continue
        module_path, folder = module
        paths = [value for _, value in go.import_paths(root)]
        found.append((
            path,
            facts(
                language=language,
                package_dir=folder,
                package_name=module_path,
                description=module_doc(folder, tree, roots),
                run_markers=[MAIN_MARKER, *programs.run_markers(language, tree[path])],
                imported_by=len(graph.get(path, [])),
                own_package_imports=sum(
                    value == module_path or value.startswith(module_path + "/")
                    for value in paths
                ),
                ui_libs=sorted({
                    library
                    for value in paths
                    for library in libraries
                    if value == library or value.startswith(library + "/")
                }),
                in_project_scripts=programs.command_folder(
                    modules.folder_of(path), folder
                ),
                test_path=False,
                imports=[f"import {node.text()}" for node, _ in go.import_paths(root)],
                referenced_by_path=launches.referenced(path, folder),
            ),
        ))
    return found


def propose(
    tree: dict[str, str], config: dict[str, Any], language: str
) -> list[tuple[str, Facts]]:
    """The facts of each TypeScript or Go file the consumer proposer reads: every file
    inside a package (TypeScript) or every program inside a module (Go)."""
    roots = {
        path: root
        for path in sorted(tree)
        if runs(path, (language,)) and (root := parsed(path, tree[path])) is not None
    }
    if language == "typescript":
        return script_facts(tree, roots, config)
    return go_facts(tree, roots, config)


def wanted_names(languages: Collection[str]) -> frozenset[str]:
    """The non-source files the consumer proposer reads for the languages: the files
    that declare commands, make a package and resolve imports."""
    names = set(importers.manifests(languages))
    for language in languages:
        names |= programs.command_manifests(language)
        if language != "python":
            names.add(programs.package_file(language))
    return frozenset(names)
