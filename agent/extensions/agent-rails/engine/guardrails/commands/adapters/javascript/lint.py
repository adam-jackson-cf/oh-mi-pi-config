from __future__ import annotations

import json
import posixpath
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters.javascript.cases import (
    SUFFIXES,
    CollectionError,
    api_name,
    arguments,
    children,
    imports,
    language,
    parse,
    qualified,
    string,
    walk,
)
from guardrails.commands.adapters.marker_bindings import inline_bindings
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.suite_files import adapter_suite, in_suite
from guardrails.foundation.findings import Finding, Remediation, remediation
from guardrails.foundation.inventory import normalized_path, repo_paths, tests_settings

if TYPE_CHECKING:
    from ast_grep_py import Config, SgNode

SKIPPED = frozenset({".git", "node_modules", "dist", "build", ".venv", "coverage"})
PATTERNS_FILE = "javascript-patterns.json"


@dataclass(frozen=True)
class Pattern:
    rule_id: str
    scope: str
    target: str
    message: str
    remediation: Remediation
    config: Config


def load_patterns() -> list[Pattern]:
    text = (
        resources
        .files("guardrails.data")
        .joinpath(PATTERNS_FILE)
        .read_text(encoding="utf-8")
    )
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
        for raw in (paths or repo_paths(repo))
        if (path := normalized_path(repo, raw)) is not None
        and path.endswith(SUFFIXES)
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


def owned_roots(config: dict[str, Any], repo: Path) -> tuple[str, ...]:
    entries = tests_settings(config, "public_entry_points") or {}
    roots = set()
    for raw in entries if isinstance(entries, dict) else ():
        path = str(raw).replace("\\", "/")
        if path.endswith(SUFFIXES):
            roots.add(posixpath.dirname(path) or ".")
        else:
            roots.add(path.rstrip("/"))
    package_names = set(roots)
    for raw in repo_paths(repo):
        manifest = normalized_path(repo, raw)
        if (
            manifest is None
            or PurePosixPath(manifest).name != "package.json"
            or SKIPPED.intersection(PurePosixPath(manifest).parts)
        ):
            continue
        data = json.loads((repo / manifest).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("name") in package_names:
            roots.add(posixpath.dirname(manifest) or ".")
    return tuple(sorted(roots))


def owned_path(path: str, roots: tuple[str, ...]) -> bool:
    return any(
        root == "." or path == root or path.startswith(root + "/") for root in roots
    )


def module_path(path: str, module: str) -> str:
    return (
        posixpath.normpath(posixpath.join(posixpath.dirname(path), module))
        if module.startswith(".")
        else module
    )


def owned_origin(path: str, origin: str, roots: tuple[str, ...]) -> bool:
    module = origin.split("#", 1)[0]
    if module.startswith("."):
        resolved = module_path(path, module)
        return not resolved.startswith("../") and owned_path(resolved, roots)
    return any(
        module == root
        or module.startswith(root + "/")
        or (
            root.startswith("src/")
            and (module == root[4:] or module.startswith(root[4:] + "/"))
        )
        for root in roots
        if root != "."
    )


def object_pairs(node: SgNode | None) -> dict[str, SgNode | None]:
    if node is None or node.kind() != "object":
        return {}
    return {
        (string(key) or key.text()): item.field("value")
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


def mock_origin(node: SgNode, aliases: Mapping[str, str]) -> str | None:
    value = string(node)
    if value is not None:
        return value
    function = node.field("function") if node.kind() == "call_expression" else None
    if function is not None and (
        function.kind() == "import" or api_name(function, aliases) == "import"
    ):
        args = arguments(node)
        return string(args[0]) if args else None
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
    if head is not None and head.kind() == "identifier" and head.text() in aliases:
        return qualified(node, aliases) or None
    return None


@dataclass(frozen=True)
class Site:
    path: str
    aliases: Mapping[str, str]
    roots: tuple[str, ...]
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
PROPERTY_RUNNERS = frozenset({
    "fast-check.assert",
    "fast-check.check",
    "fast-check#assert",
    "fast-check#check",
})
GLOBAL_CONFIGURATION = frozenset({
    "fast-check.configureGlobal",
    "fast-check#configureGlobal",
})


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
    origin = mock_origin(left, site.aliases) if left is not None else None
    if origin and owned_origin(site.path, origin, site.roots):
        return origin
    return None


def mock_call_violation(node: SgNode, site: Site) -> str | None:
    args = arguments(node)
    if (
        api_name(node.field("function"), site.aliases) not in site.vocabulary.mock_calls
        or not args
    ):
        return None
    origin = mock_origin(args[0], site.aliases)
    return origin if origin and owned_origin(site.path, origin, site.roots) else None


def evasion_violation(node: SgNode, site: Site) -> str | None:
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


def property_violation(node: SgNode, site: Site) -> str | None:
    origin = qualified(node.field("function"), site.aliases)
    if origin not in PROPERTY_RUNNERS:
        return None
    args = arguments(node)
    options = object_pairs(args[1]) if len(args) > 1 else {}
    explicit = options.get("seed")
    return (
        None if integer_seed(explicit) or (explicit is None and site.seeded) else origin
    )


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


def configured_seeds(root: SgNode, aliases: Mapping[str, str]) -> list[int]:
    configured = []
    for node in root.find_all({"rule": {"kind": "call_expression"}}):
        if qualified(node.field("function"), aliases) in GLOBAL_CONFIGURATION:
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
    path: str,
    source: str,
    patterns: list[Pattern],
    owned: tuple[str, ...],
    vocabulary: Vocabulary,
) -> list[Finding]:
    from ast_grep_py import SgRoot

    root = parse(path, source)
    inlined = SgRoot(inline_bindings(source, "typescript"), language(path)).root()
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


Definitions = dict[tuple[str, str], tuple["SgNode", "SgNode"]]
DECLARATION_KINDS = frozenset({
    "function_declaration",
    "class_declaration",
    "variable_declarator",
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


def production_definitions(
    trees: Mapping[str, SgNode],
    config: dict[str, Any],
    roots: tuple[str, ...],
    adapter: str,
) -> Definitions:
    definitions: Definitions = {}
    for path, root in trees.items():
        if is_test(path, config, adapter) or not owned_path(path, roots):
            continue
        for candidate in declarations(root):
            name = candidate.field("name")
            if name is not None and name.kind() in {"identifier", "type_identifier"}:
                definitions[(path, name.text())] = (candidate, name)
    return definitions


def in_import(node: SgNode) -> bool:
    ancestor = node.parent()
    while ancestor is not None:
        if ancestor.kind() == "import_statement":
            return True
        ancestor = ancestor.parent()
    return False


def referenced_keys(
    path: str, node: SgNode, aliases: Mapping[str, str]
) -> set[tuple[str, str]]:
    origin = qualified(node, aliases)
    if "#" in origin:
        module, symbol = origin.split("#", 1)
    elif "." in origin:
        module, _, symbol = origin.rpartition(".")
    else:
        return set()
    resolved = module_path(path, module)
    candidates = {
        resolved,
        *(resolved + suffix for suffix in SUFFIXES),
        *(resolved + "/index" + suffix for suffix in SUFFIXES),
    }
    return {(owner, symbol) for owner in candidates}


def record_uses(
    path: str,
    root: SgNode,
    test: bool,
    definitions: Definitions,
    uses: dict[tuple[str, str], set[bool]],
) -> None:
    aliases = imports(root)
    for node in walk(root):
        if node.kind() not in REFERENCE_KINDS or in_import(node):
            continue
        local_key = (path, node.text())
        own_definition = definitions.get(local_key)
        if own_definition is not None:
            declaration, _ = own_definition
            within = (
                declaration.range().start.index
                <= node.range().start.index
                <= declaration.range().end.index
            )
            if not within:
                uses[local_key].add(test)
        for key in referenced_keys(path, node, aliases):
            if key in definitions:
                uses[key].add(test)


def public_symbols(config: dict[str, Any]) -> set[tuple[str, str]]:
    entries = tests_settings(config, "public_entry_points") or {}
    public = set()
    for path, names in entries.items() if isinstance(entries, dict) else ():
        for name in names if isinstance(names, list) else []:
            if isinstance(name, str):
                public.add((path, name))
    return public


def hook_findings(
    sources: dict[str, str],
    config: dict[str, Any],
    reported: set[str],
    roots: tuple[str, ...],
    adapter: str,
) -> list[Finding]:
    trees = {path: parse(path, text) for path, text in sources.items()}
    definitions = production_definitions(trees, config, roots, adapter)
    uses: dict[tuple[str, str], set[bool]] = {key: set() for key in definitions}
    for path, root in trees.items():
        record_uses(path, root, is_test(path, config, adapter), definitions, uses)
    public = public_symbols(config)
    return [
        Finding(
            "TEST.no-test-hooks",
            path,
            f"line {declared.range().start.line + 1}: production symbol {name!r} is used only by tests",
            "remove the test-only symbol or exercise the real production API",
        )
        for (path, name), (_, declared) in definitions.items()
        if path in reported
        and uses[(path, name)] == {True}
        and (path, name) not in public
    ]


def lint(
    repo: Path,
    config: dict[str, Any],
    paths: list[str],
    vocabulary: Vocabulary,
) -> list[Finding]:
    adapter = vocabulary.adapter
    all_paths = source_files(repo, [])
    selected = set(source_files(repo, paths)) if paths else set(all_paths)
    roots = owned_roots(config, repo)
    sources = {}
    findings = []
    for path in all_paths:
        if not is_test(path, config, adapter) and not owned_path(path, roots):
            continue
        try:
            source = (repo / path).read_text(encoding="utf-8")
            parse(path, source)
            sources[path] = source
        except (OSError, UnicodeError) as error:
            rule = (
                "TEST.no-evasion"
                if is_test(path, config, adapter)
                else "TEST.no-test-hooks"
            )
            findings.append(
                Finding(
                    rule,
                    path,
                    f"cannot read source for AST analysis: {error}",
                    "restore readable UTF-8 TypeScript/JavaScript source before running adapter lint",
                )
            )
        except CollectionError as error:
            rule = (
                "TEST.no-evasion"
                if is_test(path, config, adapter)
                else "TEST.no-test-hooks"
            )
            findings.append(
                Finding(
                    rule,
                    path,
                    f"cannot parse source for AST analysis: {error}",
                    "repair JavaScript/TypeScript syntax before running adapter lint",
                )
            )
    patterns = load_patterns()
    production = set()
    for path in sorted(selected):
        if path not in sources:
            continue
        if is_test(path, config, adapter):
            scope = "tests"
        elif owned_path(path, roots):
            scope = "production"
            production.add(path)
        else:
            continue
        findings.extend(
            file_findings(
                path,
                sources[path],
                [item for item in patterns if item.scope == scope],
                roots,
                vocabulary,
            )
        )
    findings.extend(hook_findings(sources, config, production, roots, adapter))
    return apply_exceptions(findings, config)
