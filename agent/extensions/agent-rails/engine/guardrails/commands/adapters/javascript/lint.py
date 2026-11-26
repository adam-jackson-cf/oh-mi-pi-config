from __future__ import annotations

import json
from collections.abc import Callable, Container, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters import owned_source
from guardrails.commands.adapters.javascript.cases import (
    CALLBACK_KINDS,
    REGISTRARS,
    SUFFIXES,
    Imported,
    api_name,
    arguments,
    children,
    imports,
    origin,
    walk,
)
from guardrails.commands.adapters.marker_bindings import inline_bindings
from guardrails.commands.adapters.symbol_tally import Defined, Key, Tally
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.suite_files import adapter_suite, in_suite
from guardrails.foundation.findings import Finding, Remediation, remediation
from guardrails.foundation.source import tree, typescript
from guardrails.foundation.source.modules import (
    PACKAGE_MANIFEST,
    Reexport,
    ScriptResolver,
    SymbolUse,
    folder_of,
    joined,
    package_entry,
    package_manifest,
    script_file,
)

if TYPE_CHECKING:
    from ast_grep_py import Config, SgNode

DATA_FOLDER = Path(__file__).resolve().parents[4] / "data"
SKIPPED = frozenset({".git", "node_modules", "dist", "build", ".venv", "coverage"})
PATTERNS_FILE = "javascript-patterns.json"
RELATIVE_PREFIXES = ("./", "../")
RELATIVE_SPECIFIERS = frozenset({".", ".."})
# A call the test callback's context makes to skip itself (Vitest's context.skip()).
BODY_SKIPS = frozenset({"skip"})


@dataclass(frozen=True)
class Pattern:
    rule_id: str
    scope: str
    target: str
    message: str
    remediation: Remediation
    config: Config


def load_patterns() -> list[Pattern]:
    text = (DATA_FOLDER / PATTERNS_FILE).read_text(encoding="utf-8")
    return [
        Pattern(
            item["id"],
            item["scope"],
            item["target"],
            item["message"],
            remediation(item["remediation"]),
            {"rule": item["rule"]},
        )
        for item in json.loads(text)
    ]


def source_files(repo: Path, paths: list[str]) -> list[str]:
    return sorted({
        path
        for path in owned_source.repository_paths(repo, paths or None)
        if path.endswith(SUFFIXES)
        and not SKIPPED.intersection(PurePosixPath(path).parts)
    })


@dataclass(frozen=True)
class Vocabulary:
    adapter: str
    mock_calls: frozenset[str]
    evasion_flags: frozenset[str]
    skipping_registrars: frozenset[str] = frozenset()


def is_test(path: str, config: dict[str, Any], adapter: str) -> bool:
    return in_suite(path, adapter_suite(config, adapter))


@dataclass(frozen=True)
class Package:
    """A package.json package: its folder and the file a bare import of it names."""

    folder: str
    entry: str | None


def package_manifests(
    repo: Path, tracked: list[str]
) -> tuple[dict[str, Package], list[Finding]]:
    """Each package.json name in the repository with its package, and a finding for each
    package.json that does not read as a JSON object."""
    found: dict[str, Package] = {}
    problems: list[Finding] = []
    for manifest in tracked:
        pure = PurePosixPath(manifest)
        if pure.name != PACKAGE_MANIFEST or SKIPPED.intersection(pure.parts):
            continue
        try:
            data = package_manifest(manifest, tree.read(repo, manifest))
        except owned_source.READ_ERRORS as error:
            problems.append(owned_source.manifest_finding(manifest, error))
            continue
        if isinstance(data.get("name"), str):
            found[data["name"]] = Package(folder_of(manifest), package_entry(data))
    return found, problems


def owned_roots(
    points: owned_source.EntryPoints, packages: Mapping[str, Package]
) -> tuple[str, ...]:
    """The owned folders: each entry point's package as a folder, or the folder of the
    package.json it names."""
    roots = {point.package.rstrip("/") for point in points}
    named = {package.folder for name, package in packages.items() if name in roots}
    return tuple(sorted(roots | named))


def owned_path(path: str, roots: tuple[str, ...]) -> bool:
    return any(
        root == "." or path == root or path.startswith(root + "/") for root in roots
    )


def owned_packages(
    packages: Mapping[str, Package], roots: tuple[str, ...]
) -> dict[str, Package]:
    return {
        name: package
        for name, package in packages.items()
        if owned_path(package.folder, roots)
    }


def package_location(specifier: str, owned: Owned) -> tuple[Package, str] | None:
    """The owned package a bare specifier names, with the subpath after its name."""
    for name, package in owned.packages.items():
        if specifier == name or specifier.startswith(name + "/"):
            return package, specifier[len(name) :].lstrip("/")
    return None


def package_target(specifier: str, owned: Owned) -> str | None:
    """The script a bare specifier names in an owned package: the package's exports or
    main entry for its name, a file or folder index under it for a subpath."""
    found = package_location(specifier, owned)
    if found is None:
        return None
    package, subpath = found
    relative = subpath or package.entry
    base = None if relative is None else joined(package.folder, relative)
    return None if base is None else script_file(base, owned.scripts.tracked)


def owned_target(path: str, specifier: str, owned: Owned) -> str | None:
    """The script an import specifier names: through the resolver (relative paths and
    tsconfig paths), then as an owned package name. Symbol uses resolve by it."""
    target = owned.scripts.resolve(path, specifier)
    return target if target is not None else package_target(specifier, owned)


def relative_specifier(specifier: str) -> bool:
    return specifier in RELATIVE_SPECIFIERS or specifier.startswith(RELATIVE_PREFIXES)


def owned_location(path: str, specifier: str, owned: Owned) -> str | None:
    """The path a specifier names, for ownership: a relative one by its path whatever
    file it is (a stylesheet or JSON data too), a package one by the package folder,
    any other through the resolver."""
    if relative_specifier(specifier):
        return joined(folder_of(path), specifier)
    found = package_location(specifier, owned)
    if found is not None:
        package, subpath = found
        return joined(package.folder, subpath or ".")
    return owned.scripts.resolve(path, specifier)


def owned_origin(path: str, found: Imported, owned: Owned) -> bool:
    location = owned_location(path, found.module, owned)
    return location is not None and owned_path(location, owned.roots)


def object_pairs(node: SgNode | None) -> dict[str, SgNode | None]:
    if node is None or node.kind() != "object":
        return {}
    return {
        (typescript.literal(key) or key.text()): item.field("value")
        for item in children(node)
        if item.kind() == "pair" and (key := item.field("key")) is not None
    }


def integer_seed(node: SgNode | None) -> bool:
    if node is None:
        return False
    try:
        value = json.loads(node.text())
    except ValueError:
        return False
    return type(value) is int


def mock_origin(node: SgNode, aliases: Mapping[str, Imported]) -> Imported | None:
    value = typescript.literal(node)
    if value is not None:
        return Imported(value, None)
    function = node.field("function") if node.kind() == "call_expression" else None
    if function is not None and (
        function.kind() == "import" or api_name(function, aliases) == "import"
    ):
        args = arguments(node)
        module = typescript.literal(args[0]) if args else None
        return None if module is None else Imported(module, None)
    if node.kind() == "new_expression":
        constructor = node.field("constructor")
        return mock_origin(constructor, aliases) if constructor is not None else None
    if function is not None:
        return mock_origin(function, aliases)
    head: SgNode | None = node
    while head is not None and head.kind() in {
        "member_expression",
        "subscript_expression",
    }:
        head = head.field("object")
    if head is not None and head.kind() == "identifier":
        return origin(node, aliases)
    return None


@dataclass(frozen=True)
class Owned:
    """The owned folders, the owned packages by name, and how imports reach them."""

    roots: tuple[str, ...]
    packages: Mapping[str, Package]
    scripts: ScriptResolver


@dataclass(frozen=True)
class Site:
    path: str
    aliases: Mapping[str, Imported]
    owned: Owned
    seeded: bool
    vocabulary: Vocabulary


HOOK_DECLARATIONS = frozenset({
    "variable_declarator",
    "function_declaration",
    "class_declaration",
    "method_definition",
    "required_parameter",
    "optional_parameter",
})
PROPERTY_MODULE = "fast-check"
PROPERTY_RUNNERS = frozenset({("assert",), ("check",)})
GLOBAL_CONFIGURATION = ("configureGlobal",)


def environment_violation(node: SgNode, site: Site) -> str | None:
    return node.text()


def hook_violation(node: SgNode, site: Site) -> str | None:
    parent = node.parent()
    if parent is not None and parent.kind() in HOOK_DECLARATIONS:
        name = parent.field("name") or parent.field("pattern")
        if name is not None and name.range().start.index == node.range().start.index:
            return node.text()
    return None


def mock_assignment_violation(node: SgNode, site: Site) -> str | None:
    left = node.field("left")
    found = mock_origin(left, site.aliases) if left is not None else None
    if found is not None and owned_origin(site.path, found, site.owned):
        return found.label()
    return None


def mock_call_violation(node: SgNode, site: Site) -> str | None:
    args = arguments(node)
    if (
        api_name(node.field("function"), site.aliases) not in site.vocabulary.mock_calls
        or not args
    ):
        return None
    found = mock_origin(args[0], site.aliases)
    if found is not None and owned_origin(site.path, found, site.owned):
        return found.label()
    return None


def callback_context(callback: SgNode) -> str | None:
    """The name of a test callback's first parameter, the test context."""
    parameters = callback.field("parameters") or callback.field("parameter")
    if parameters is None:
        return None
    first = (
        parameters
        if parameters.kind() == "identifier"
        else next(iter(children(parameters)), None)
    )
    pattern = None if first is None else first.field("pattern") or first
    return (
        pattern.text()
        if pattern is not None and pattern.kind() == "identifier"
        else None
    )


def body_skip(node: SgNode, site: Site) -> str | None:
    """An unconditional context.skip(): a statement of the test callback's own body, so
    the test never runs. A skip inside a condition is a runtime skip the run report
    records."""
    function = node.field("function")
    target = function.field("object") if function is not None else None
    member = function.field("property") if function is not None else None
    statement = node.parent()
    block = statement.parent() if statement is not None else None
    callback = block.parent() if block is not None else None
    arguments_node = callback.parent() if callback is not None else None
    registration = arguments_node.parent() if arguments_node is not None else None
    if (
        target is None
        or member is None
        or member.text() not in BODY_SKIPS
        or statement is None
        or statement.kind() != "expression_statement"
        or block is None
        or block.kind() != "statement_block"
        or callback is None
        or callback.kind() not in CALLBACK_KINDS
        or registration is None
        or registration.kind() != "call_expression"
        or api_name(registration.field("function"), site.aliases).split(".")[0]
        not in REGISTRARS
    ):
        return None
    context = callback_context(callback)
    return f"{context}.{member.text()}" if context == target.text() else None


def evasion_violation(node: SgNode, site: Site) -> str | None:
    skipped = body_skip(node, site)
    if skipped is not None:
        return skipped
    name = api_name(node.field("function"), site.aliases)
    vocabulary = site.vocabulary
    if name.split(".")[0] in vocabulary.skipping_registrars:
        return name
    if name.split(".")[0] not in {"test", "it", "describe", "suite"}:
        return None
    if set(name.split(".")) & vocabulary.evasion_flags:
        return name
    for arg in arguments(node):
        retry = object_pairs(arg).get("retry")
        if retry is not None and retry.text() != "0":
            return "retry=" + retry.text()
    return None


def property_call(found: Imported | None, paths: Container[tuple[str, ...]]) -> bool:
    return (
        found is not None and found.module == PROPERTY_MODULE and found.path() in paths
    )


def property_violation(node: SgNode, site: Site) -> str | None:
    found = origin(node.field("function"), site.aliases)
    if found is None or not property_call(found, PROPERTY_RUNNERS):
        return None
    args = arguments(node)
    options = object_pairs(args[1]) if len(args) > 1 else {}
    explicit = options.get("seed")
    seeded = integer_seed(explicit) or (explicit is None and site.seeded)
    return None if seeded else found.label()


VIOLATIONS: dict[str, Callable[[SgNode, Site], str | None]] = {
    "environment": environment_violation,
    "hook": hook_violation,
    "mock-assignment": mock_assignment_violation,
    "mock-call": mock_call_violation,
    "evasion": evasion_violation,
    "property": property_violation,
}


def violation(pattern: Pattern, node: SgNode, site: Site) -> str | None:
    check = VIOLATIONS.get(pattern.target)
    if check is None:
        raise ValueError(f"unknown JavaScript pattern target {pattern.target}")
    return check(node, site)


def configured_seeds(root: SgNode, aliases: Mapping[str, Imported]) -> list[int]:
    configured = []
    for node in root.find_all({"rule": {"kind": "call_expression"}}):
        if property_call(
            origin(node.field("function"), aliases), {GLOBAL_CONFIGURATION}
        ):
            args = arguments(node)
            parent = node.parent()
            grandparent = parent.parent() if parent is not None else None
            top_level = (
                parent is not None
                and parent.kind() == "expression_statement"
                and grandparent is not None
                and grandparent.kind() == "program"
            )
            if top_level and args and integer_seed(object_pairs(args[0]).get("seed")):
                configured.append(node.range().end.index)
    return configured


def file_findings(
    source: owned_source.Source,
    patterns: list[Pattern],
    owned: Owned,
    vocabulary: Vocabulary,
) -> list[Finding]:
    path, root = source.path, source.root
    inlined = tree.tolerant_tree(
        inline_bindings(source.text, "typescript"), tree.grammar(path)
    )
    aliases = imports(root)
    configured = configured_seeds(root, aliases)
    site = Site(path, aliases, owned, False, vocabulary)
    findings = []
    for pattern in patterns:
        scanned = inlined if pattern.target == "environment" else root
        findings.extend(pattern_findings(pattern, scanned, configured, site))
    return findings


def pattern_findings(
    pattern: Pattern, root: SgNode, configured: list[int], site: Site
) -> list[Finding]:
    findings = []
    for node in root.find_all(pattern.config):
        seeded = any(end < node.range().start.index for end in configured)
        target = violation(pattern, node, replace(site, seeded=seeded))
        if target is not None:
            findings.append(
                Finding(
                    pattern.rule_id,
                    site.path,
                    f"line {node.range().start.line + 1}: "
                    + pattern.message.format(target=target),
                    pattern.remediation.render(),
                )
            )
    return findings


DECLARATION_KINDS = frozenset({
    "function_declaration",
    "class_declaration",
    "variable_declarator",
})
BINDING_PATTERNS = frozenset({
    "object_pattern",
    "pair_pattern",
    "object_assignment_pattern",
})
REFERENCE_KINDS = frozenset({
    "identifier",
    "shorthand_property_identifier",
    "member_expression",
})


def declarations(root: SgNode) -> Iterator[SgNode]:
    for statement in children(root):
        for node in (
            children(statement)
            if statement.kind() == "export_statement"
            else [statement]
        ):
            yield from declared_nodes(node)


def declared_nodes(node: SgNode) -> list[SgNode]:
    candidates = (
        children(node)
        if node.kind() in {"lexical_declaration", "variable_declaration"}
        else [node]
    )
    return [
        candidate for candidate in candidates if candidate.kind() in DECLARATION_KINDS
    ]


def production_definitions(production: Mapping[str, SgNode]) -> dict[Key, Defined]:
    definitions: dict[Key, Defined] = {}
    for path, root in production.items():
        for candidate in declarations(root):
            name = candidate.field("name")
            if name is not None and name.kind() in {"identifier", "type_identifier"}:
                span = (candidate.range().start.index, candidate.range().end.index)
                definitions[path, name.text()] = Defined(
                    name.range().start.line + 1, span
                )
    return definitions


def import_statement(node: SgNode) -> bool:
    return node.kind() == "import_statement" or (
        node.kind() == "export_statement" and node.field("source") is not None
    )


def load_binding(node: SgNode) -> bool:
    """Whether node is a name a require() or import() declarator binds."""
    child, parent = node, node.parent()
    while parent is not None and parent.kind() in BINDING_PATTERNS:
        child, parent = parent, parent.parent()
    return (
        parent is not None
        and parent.kind() == "variable_declarator"
        and typescript.same(parent.field("name"), child)
        and typescript.load_call(parent.field("value")) is not None
    )


def in_import(node: SgNode) -> bool:
    """Whether node names a binding of an import rather than a use of it."""
    ancestor = node.parent()
    while ancestor is not None:
        if import_statement(ancestor):
            return True
        ancestor = ancestor.parent()
    return load_binding(node)


def reexports(path: str, root: SgNode, owned: Owned) -> list[Reexport]:
    """The names a file re-exports with export-from, each from the script it names."""
    found = []
    for site in typescript.imports(root):
        for binding in site.bindings:
            target = (
                owned_target(path, binding.specifier, owned)
                if binding.form == "export-from"
                else None
            )
            if target is None or (binding.imported is None) != (binding.local is None):
                continue
            found.append(Reexport(binding.local, target, binding.imported))
    return found


def file_uses(
    path: str, root: SgNode, owned: Owned, definitions: Mapping[Key, Defined]
) -> list[SymbolUse]:
    """The file's own names it reads, and each import's export it reads."""
    aliases = imports(root)
    uses = []
    for node in walk(root):
        if node.kind() not in REFERENCE_KINDS or in_import(node):
            continue
        at = node.range().start.index
        if (path, node.text()) in definitions:
            uses.append(SymbolUse(path, node.text(), at))
        found = origin(node, aliases)
        symbol = None if found is None else found.symbol()
        owner = None if found is None else owned_target(path, found.module, owned)
        if owner is not None and symbol is not None:
            uses.append(SymbolUse(owner, symbol, at))
    return uses


def entry_script(module: str, owned: Owned) -> str | None:
    """The script an entry point's module names: a repository path, or a package."""
    base = joined(".", module)
    found = None if base is None else script_file(base, owned.scripts.tracked)
    return found if found is not None else package_target(module, owned)


def test_only_findings(
    sources: Mapping[str, owned_source.Source],
    is_test: Callable[[str], bool],
    reported: set[str],
    owned: Owned,
    points: owned_source.EntryPoints,
) -> list[Finding]:
    trees = {path: source.root for path, source in sources.items()}
    production = {path: root for path, root in trees.items() if not is_test(path)}
    tally = Tally(production_definitions(production))
    tally.reexports.update(
        (path, reexports(path, root, owned)) for path, root in trees.items()
    )
    for path, root in sorted(trees.items()):
        tally.record(
            path, file_uses(path, root, owned, tally.definitions), test=is_test(path)
        )
    public = tally.public(
        (script, point.symbol)
        for point in points
        if (script := entry_script(point.module, owned)) is not None
    )
    return tally.findings(reported, public)


def tsconfig_findings(scripts: ScriptResolver, paths: list[str]) -> list[Finding]:
    found = []
    for path in sorted({
        config for path in paths if (config := scripts.config_path(path)) is not None
    }):
        try:
            scripts.config(path)
        except owned_source.READ_ERRORS as error:
            found.append(owned_source.manifest_finding(path, error))
    return found


@dataclass(frozen=True)
class Scan:
    """The test and owned files a lint run reads, the findings for manifests and files
    it cannot read, and how imports reach owned code."""

    sources: dict[str, owned_source.Source]
    unreadable: list[Finding]
    manifests: list[Finding]
    owned: Owned


def scan(
    repo: Path,
    repo_files: list[str],
    points: owned_source.EntryPoints,
    is_test_file: Callable[[str], bool],
) -> Scan:
    packages, problems = package_manifests(repo, repo_files)
    roots = owned_roots(points, packages)
    read = [
        path
        for path in source_files(repo, repo_files)
        if is_test_file(path) or owned_path(path, roots)
    ]
    sources, unreadable = owned_source.load_all(repo, read, is_test_file)
    scripts = ScriptResolver(set(repo_files), lambda path: tree.read(repo, path))
    problems.extend(tsconfig_findings(scripts, sorted(sources)))
    owned = Owned(roots, owned_packages(packages, roots), scripts)
    return Scan(sources, unreadable, problems, owned)


def lint(
    repo: Path,
    config: dict[str, Any],
    paths: list[str],
    vocabulary: Vocabulary,
) -> list[Finding]:
    points = owned_source.configured_entry_points(config)
    if isinstance(points, Finding):
        return [points]

    def is_test_file(path: str) -> bool:
        return is_test(path, config, vocabulary.adapter)

    repo_files = owned_source.repository_paths(repo)
    selected = set(source_files(repo, paths or repo_files))
    found = scan(repo, repo_files, points, is_test_file)
    findings = [item for item in found.unreadable if item.path in selected]
    if found.manifests:
        return apply_exceptions(findings + found.manifests, config)
    patterns = load_patterns()
    production = set()
    for path in sorted(selected & found.sources.keys()):
        scope = "tests" if is_test_file(path) else "production"
        if scope == "production":
            production.add(path)
        findings.extend(
            file_findings(
                found.sources[path],
                [item for item in patterns if item.scope == scope],
                found.owned,
                vocabulary,
            )
        )
    if production:
        findings.extend(
            test_only_findings(
                found.sources, is_test_file, production, found.owned, points
            )
        )
    return apply_exceptions(findings, config)
