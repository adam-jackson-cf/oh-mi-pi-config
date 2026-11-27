from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters import hook_vocabulary, owned_source
from guardrails.commands.adapters.go.dialect import XFAIL_PREFIX, vocabulary
from guardrails.commands.adapters.go.ownership import (
    GoModules,
    owned_import,
    owned_imports,
    owned_path,
    owned_roots,
)
from guardrails.commands.adapters.go.symbol_usage import test_only_findings
from guardrails.commands.adapters.go.syntax import (
    arguments,
    call_name,
    first_parameter,
    go_files,
    test_functions,
    walk,
)
from guardrails.commands.adapters.marker_bindings import inline_bindings
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding
from guardrails.foundation.source import go as go_source, tree

if TYPE_CHECKING:
    from ast_grep_py import SgNode

SELF_MOCKING = "TEST.no-self-mocking"
EVASION = "TEST.no-evasion"
SEED = "TEST.property-seed"
HOOKS = "TEST.no-test-hooks"
COMPARISONS = frozenset({"==", "!="})
MARKER_MESSAGE = (
    "production code reads a test marker from the environment or test flags ({})"
)
DECLARING = frozenset({
    "function_declaration",
    "method_declaration",
    "parameter_declaration",
    "field_declaration",
    "var_spec",
    "const_spec",
    "type_spec",
})
NAMES = frozenset({"identifier", "field_identifier", "type_identifier"})


def names(key: str) -> frozenset[str]:
    return frozenset(vocabulary()[key])


def calls_named(key: str) -> frozenset[tuple[str, str]]:
    """Calls as (package import path, function), such as os.Getenv."""
    return frozenset((package, name) for package, name in vocabulary()[key])


def fix(rule: str) -> str:
    text: str = vocabulary()["fixes"][rule]
    return text


@dataclass(frozen=True)
class Source:
    path: str
    root: SgNode
    imports: dict[str, str]


def line(node: SgNode) -> int:
    return node.range().start.line + 1


def snippet(node: SgNode) -> str:
    return " ".join(node.text().split())[:80]


def owned_aliases(source: Source, owned: tuple[str, ...]) -> frozenset[str]:
    return frozenset(
        alias for alias, path in source.imports.items() if owned_import(path, owned)
    )


def references_owned(node: SgNode, aliases: frozenset[str]) -> bool:
    for item in walk(node):
        if item.kind() == "selector_expression":
            operand = item.field("operand")
            if operand is not None and operand.text() in aliases:
                return True
        if item.kind() == "qualified_type":
            package = item.field("package")
            if package is not None and package.text() in aliases:
                return True
    return False


def calls(root: SgNode) -> list[SgNode]:
    return [node for node in walk(root) if node.kind() == "call_expression"]


def self_mocking(
    source: Source, owned: tuple[str, ...], owned_types: frozenset[str]
) -> list[Finding]:
    aliases = owned_aliases(source, owned)
    found = []
    for call in calls(source.root):
        qualifier, name = call_name(call)
        patching = qualifier is not None and source.imports.get(qualifier) in names(
            "patch_packages"
        )
        if patching and any(
            references_owned(item, aliases) for item in arguments(call)
        ):
            found.append((call, f"patches owned code ({snippet(call)})"))
        mock = re.match(vocabulary()["mock_constructor"], name or "")
        if mock and mock.group(1) in owned_types:
            found.append((call, f"builds a mock of owned type {mock.group(1)}"))
    for node in walk(source.root):
        if node.kind() != "assignment_statement":
            continue
        left = node.field("left")
        targets = tree.named(left) if left is not None else []
        if any(
            target.kind() == "selector_expression"
            and (operand := target.field("operand")) is not None
            and operand.text() in aliases
            for target in targets
        ):
            found.append((node, f"rebinds owned package state ({snippet(node)})"))
    return [
        Finding(
            SELF_MOCKING,
            source.path,
            f"line {line(node)}: {message}",
            fix(SELF_MOCKING),
        )
        for node, message in found
    ]


def skip_call(call: SgNode, receiver: str | None) -> bool:
    qualifier, name = call_name(call)
    return name in names("skip_calls") and (receiver is None or qualifier == receiver)


def body_statements(function: SgNode) -> list[SgNode]:
    body = function.field("body")
    blocks = tree.named(body) if body is not None else []
    return [
        statement
        for block in blocks
        if block.kind() == "statement_list"
        for statement in tree.named(block)
    ]


def static_skips(function: SgNode) -> list[SgNode]:
    receiver = first_parameter(function)
    expressions = [
        tree.named(statement)[0]
        for statement in body_statements(function)
        if statement.kind() == "expression_statement" and tree.named(statement)
    ]
    return [
        call
        for call in expressions
        if call.kind() == "call_expression" and skip_call(call, receiver)
    ]


def evasion(source: Source) -> list[Finding]:
    found = [
        (call, f"static skip ({snippet(call)})")
        for function in test_functions(source.root)
        for call in static_skips(function)
    ]
    for call in calls(source.root):
        values = arguments(call)
        reason = go_source.string_value(values[0]) if values else None
        if (
            skip_call(call, None)
            and reason is not None
            and reason.startswith(XFAIL_PREFIX)
        ):
            found.append((call, f"expected-failure marker ({snippet(call)})"))
        qualifier, _ = call_name(call)
        if qualifier is not None and source.imports.get(qualifier) in names(
            "retry_packages"
        ):
            found.append((call, f"retries until green ({snippet(call)})"))
    unique = {(line(node), message): node for node, message in found}
    return [
        Finding(EVASION, source.path, f"line {number}: {message}", fix(EVASION))
        for number, message in sorted(unique)
    ]


def config_literal(node: SgNode, root: SgNode) -> SgNode | None:
    if node.kind() == "unary_expression":
        operand = node.field("operand")
        return (
            operand
            if operand is not None and operand.kind() == "composite_literal"
            else None
        )
    if node.kind() == "composite_literal":
        return node
    if node.kind() != "identifier":
        return None
    for item in walk(root):
        if item.kind() not in {
            "short_var_declaration",
            "assignment_statement",
            "var_spec",
        }:
            continue
        names = [child for child in walk(item) if child.kind() == "identifier"]
        if names and names[0].text() == node.text():
            for child in walk(item):
                if child.kind() == "composite_literal":
                    return child
    return None


def seeded(literal: SgNode | None) -> bool:
    if literal is None:
        return False
    for item in walk(literal):
        if item.kind() == "keyed_element":
            key = tree.named(item)[0] if tree.named(item) else None
            if key is not None and key.text() == vocabulary()["property"]["seed_field"]:
                return True
    return False


def property_seed(source: Source) -> list[Finding]:
    found = []
    quick = vocabulary()["property"]
    for call in calls(source.root):
        qualifier, name = call_name(call)
        if qualifier is None or source.imports.get(qualifier) != quick["package"]:
            continue
        if name not in quick["checks"]:
            continue
        values = arguments(call)
        options = config_literal(values[-1], source.root) if values else None
        if not seeded(options):
            found.append(
                Finding(
                    SEED,
                    source.path,
                    f"line {line(call)}: quick property test neither fixes nor reports "
                    f"its seed ({snippet(call)})",
                    fix(SEED),
                )
            )
    return found


def marker_name(value: str) -> bool:
    """A test marker an environment read or a flag lookup names: a shared marker name
    (test-hook-patterns.json) or a go test flag (test.v)."""
    return hook_vocabulary.matches("marker_name", value) or (
        re.fullmatch(vocabulary()["flag_marker"], value) is not None
    )


def reads(call: SgNode | None, source: Source, key: str) -> bool:
    """Whether a node is a call of one of the vocabulary's calls (by import path)."""
    if call is None or call.kind() != "call_expression":
        return False
    qualifier, name = call_name(call)
    return qualifier is not None and (
        source.imports.get(qualifier),
        name,
    ) in calls_named(key)


def marker_read(node: SgNode, source: Source) -> bool:
    argument_list = node.parent()
    call = argument_list.parent() if argument_list is not None else None
    if (
        argument_list is None
        or argument_list.kind() != "argument_list"
        or call is None
        or call.kind() != "call_expression"
    ):
        return False
    return reads(call, source, "marker_reads")


def value_comparison(node: SgNode, source: Source) -> bool:
    """Whether a node compares an environment value with a test marker value, as in
    os.Getenv("APP_ENV") == "test"."""
    operator = node.field("operator")
    if node.kind() != "binary_expression" or operator is None:
        return False
    sides = [node.field("left"), node.field("right")]
    return operator.text() in COMPARISONS and any(
        (value := go_source.string_value(side)) is not None
        and hook_vocabulary.matches("marker_value", value)
        and reads(other, source, "value_reads")
        for side, other in (sides, sides[::-1])
    )


def marker_reads(source: Source) -> list[tuple[SgNode, str]]:
    inlined = tree.tolerant_tree(
        inline_bindings(source.root.text(), source.root, "go"), "go"
    )
    return [
        (node, MARKER_MESSAGE.format(snippet(node)))
        for node in walk(inlined)
        if (
            (value := go_source.string_value(node)) is not None
            and marker_name(value)
            and marker_read(node, source)
        )
        or value_comparison(node, source)
    ]


def hook_findings(source: Source) -> list[Finding]:
    found = []
    probe = tuple(vocabulary()["test_probe"])
    for call in calls(source.root):
        qualifier, name = call_name(call)
        if qualifier is not None and (source.imports.get(qualifier), name) == probe:
            found.append((
                call,
                f"production code asks whether it runs under test ({snippet(call)})",
            ))
    found.extend(marker_reads(source))
    for node in walk(source.root):
        if node.kind() in DECLARING:
            for item in tree.named(node):
                if item.kind() in NAMES and hook_vocabulary.hook_phrase(item.text()):
                    found.append((
                        item,
                        f"production code declares a test-only hook ({item.text()})",
                    ))
    unique = {(line(node), message) for node, message in found}
    return [
        Finding(HOOKS, source.path, f"line {number}: {message}", fix(HOOKS))
        for number, message in sorted(unique)
    ]


def type_names(root: SgNode) -> set[str]:
    specs = [
        spec
        for node in tree.named(root)
        if node.kind() == "type_declaration"
        for spec in tree.named(node)
        if spec.kind() == "type_spec"
    ]
    return {name.text() for spec in specs if (name := spec.field("name")) is not None}


def owned_type_names(sources: dict[str, Source]) -> frozenset[str]:
    return frozenset(
        name for source in sources.values() for name in type_names(source.root)
    )


def built_sources(
    loaded: dict[str, owned_source.Source],
) -> tuple[dict[str, Source], list[Finding]]:
    """The loaded files the go tool builds (a //go:build ignore file is no package's
    source), each with its imports; a file with no package clause is unreadable."""
    built: dict[str, Source] = {}
    findings: list[Finding] = []
    for path, loaded_source in loaded.items():
        root = loaded_source.root
        try:
            if go_source.build_ignored(root):
                continue
        except tree.SourceError as error:
            test = go_source.is_test_file(path)
            findings.append(owned_source.unparseable(path, error, test=test))
            continue
        built[path] = Source(path, root, go_source.import_aliases(root))
    return built, findings


@dataclass(frozen=True)
class Scan:
    """The test and owned Go files a lint run reads, the findings for those it cannot,
    and the modules and owned folders they resolve in."""

    built: dict[str, Source]
    unreadable: list[Finding]
    modules: GoModules
    roots: tuple[str, ...]


def scan(repo: Path, tracked: list[str], roots: tuple[str, ...]) -> Scan:
    is_test = go_source.is_test_file
    read = [
        path for path in go_files(tracked) if is_test(path) or owned_path(path, roots)
    ]
    loaded, unreadable = owned_source.load_all(repo, read, is_test)
    built, broken = built_sources(loaded)
    return Scan(built, [*unreadable, *broken], GoModules(repo, set(tracked)), roots)


def file_lint(
    source: Source, found: Scan, owned_types: frozenset[str]
) -> list[Finding]:
    if go_source.is_test_file(source.path):
        owned = owned_imports(found.roots, found.modules)
        return [
            *self_mocking(source, owned, owned_types),
            *evasion(source),
            *property_seed(source),
        ]
    return hook_findings(source)


def lint(repo: Path, config: dict[str, Any], paths: list[str]) -> list[Finding]:
    points = owned_source.configured_entry_points(config)
    if isinstance(points, Finding):
        return [points]
    tracked = owned_source.repository_paths(repo)
    chosen = set(
        go_files(owned_source.repository_paths(repo, paths) if paths else tracked)
    )
    found = scan(repo, tracked, owned_roots(points))
    production = {
        path: item
        for path, item in found.built.items()
        if not go_source.is_test_file(path)
    }
    owned_types = owned_type_names(production)
    findings = [item for item in found.unreadable if item.path in chosen]
    for path in sorted(chosen & found.built.keys()):
        findings.extend(file_lint(found.built[path], found, owned_types))
    reported = chosen & production.keys()
    if reported:
        trees = {path: item.root for path, item in found.built.items()}
        findings.extend(
            test_only_findings(
                trees, go_source.is_test_file, reported, found.modules, points
            )
        )
    findings.extend(found.modules.problems.values())
    return apply_exceptions(findings, config)
