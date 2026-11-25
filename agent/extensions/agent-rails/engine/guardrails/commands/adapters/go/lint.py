from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters.go.dialect import XFAIL_PREFIX
from guardrails.commands.adapters.go.ownership import (
    owned_import,
    owned_imports,
    owned_path,
)
from guardrails.commands.adapters.go.syntax import (
    arguments,
    call_name,
    children,
    first_parameter,
    go_files,
    has_errors,
    imports,
    is_test_path,
    parse,
    string_value,
    test_functions,
    walk,
    words,
)
from guardrails.commands.adapters.marker_bindings import inline_bindings
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import normalized_path, repo_paths, tests_settings

if TYPE_CHECKING:
    from ast_grep_py import SgNode

SELF_MOCKING = "TEST.no-self-mocking"
EVASION = "TEST.no-evasion"
SEED = "TEST.property-seed"
HOOKS = "TEST.no-test-hooks"
SKIP_CALLS = frozenset({"Skip", "Skipf", "SkipNow"})
PATCH_PACKAGES = frozenset({
    "github.com/agiledragon/gomonkey",
    "github.com/agiledragon/gomonkey/v2",
    "bou.ke/monkey",
    "github.com/bouk/monkey",
    "github.com/undefinedlabs/go-mpatch",
})
RETRY_PACKAGES = frozenset({
    "github.com/hashicorp/consul/sdk/testutil/retry",
    "github.com/avast/retry-go",
    "github.com/avast/retry-go/v2",
    "github.com/avast/retry-go/v3",
    "github.com/avast/retry-go/v4",
})
QUICK_PACKAGE = "testing/quick"
QUICK_CHECKS = frozenset({"Check", "CheckEqual"})
TESTING_PACKAGE = "testing"
MARKER_READS = frozenset({("os", "Getenv"), ("os", "LookupEnv"), ("flag", "Lookup")})
MARKER = re.compile(
    r"^([A-Z0-9_]*_)?(GO_?TEST[A-Z0-9_]*|TESTING|TEST_MODE|UNDER_TEST|TESTS?)(_[A-Z0-9_]*)?$"
    r"|^test\.\w+$"
)
HOOK_PHRASES = frozenset({
    ("for", "test"),
    ("for", "tests"),
    ("for", "testing"),
    ("test", "hook"),
    ("test", "hooks"),
    ("testing", "only"),
    ("test", "only"),
    ("test", "mode"),
})
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
MOCK_CONSTRUCTOR = re.compile(r"^NewMock(\w+)$")
SELF_MOCKING_FIX = (
    "test the owned code through its public entry point; fake only boundaries"
)
EVASION_FIX = "make the test run and pass, or record an owner-approved exception"
SEED_FIX = "give quick.Config a Rand built from a fixed seed"
HOOKS_FIX = "remove the hook; tests must exercise the production path unchanged"
SYMBOL_FIX = (
    "drive the behaviour through a public entry point and delete the symbol, or give it a "
    "production caller; production code never carries hooks only tests use"
)


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
        patching = (
            qualifier is not None and source.imports.get(qualifier) in PATCH_PACKAGES
        )
        if patching and any(
            references_owned(item, aliases) for item in arguments(call)
        ):
            found.append((call, f"patches owned code ({snippet(call)})"))
        mock = MOCK_CONSTRUCTOR.match(name or "")
        if mock and mock.group(1) in owned_types:
            found.append((call, f"builds a mock of owned type {mock.group(1)}"))
    for node in walk(source.root):
        if node.kind() != "assignment_statement":
            continue
        left = node.field("left")
        targets = children(left) if left is not None else []
        if any(
            target.kind() == "selector_expression"
            and (operand := target.field("operand")) is not None
            and operand.text() in aliases
            for target in targets
        ):
            found.append((node, f"rebinds owned package state ({snippet(node)})"))
    return [
        Finding(
            SELF_MOCKING, source.path, f"line {line(node)}: {message}", SELF_MOCKING_FIX
        )
        for node, message in found
    ]


def skip_call(call: SgNode, receiver: str | None) -> bool:
    qualifier, name = call_name(call)
    return name in SKIP_CALLS and (receiver is None or qualifier == receiver)


def body_statements(function: SgNode) -> list[SgNode]:
    body = function.field("body")
    blocks = children(body) if body is not None else []
    return [
        statement
        for block in blocks
        if block.kind() == "statement_list"
        for statement in children(block)
    ]


def static_skips(function: SgNode) -> list[SgNode]:
    receiver = first_parameter(function)
    expressions = [
        children(statement)[0]
        for statement in body_statements(function)
        if statement.kind() == "expression_statement" and children(statement)
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
        reason = string_value(values[0]) if values else None
        if (
            skip_call(call, None)
            and reason is not None
            and reason.startswith(XFAIL_PREFIX)
        ):
            found.append((call, f"expected-failure marker ({snippet(call)})"))
        qualifier, _ = call_name(call)
        if qualifier is not None and source.imports.get(qualifier) in RETRY_PACKAGES:
            found.append((call, f"retries until green ({snippet(call)})"))
    unique = {(line(node), message): node for node, message in found}
    return [
        Finding(EVASION, source.path, f"line {number}: {message}", EVASION_FIX)
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
            key = children(item)[0] if children(item) else None
            if key is not None and key.text() == "Rand":
                return True
    return False


def property_seed(source: Source) -> list[Finding]:
    found = []
    for call in calls(source.root):
        qualifier, name = call_name(call)
        if qualifier is None or source.imports.get(qualifier) != QUICK_PACKAGE:
            continue
        if name not in QUICK_CHECKS:
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
                    SEED_FIX,
                )
            )
    return found


def hook_phrase(name: str) -> bool:
    parts = words(name)
    return any(pair in HOOK_PHRASES for pair in zip(parts, parts[1:], strict=False))


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
    qualifier, name = call_name(call)
    return (
        qualifier is not None and (source.imports.get(qualifier), name) in MARKER_READS
    )


def marker_reads(source: Source) -> list[tuple[SgNode, str]]:
    return [
        (
            node,
            "production code reads a test marker from the environment or test flags "
            f"({snippet(node)})",
        )
        for node in walk(parse(inline_bindings(source.root.text(), "go")))
        if (value := string_value(node)) is not None
        and MARKER.match(value)
        and marker_read(node, source)
    ]


def hook_findings(source: Source) -> list[Finding]:
    found = []
    for call in calls(source.root):
        qualifier, name = call_name(call)
        if (
            qualifier is not None
            and source.imports.get(qualifier) == TESTING_PACKAGE
            and name == "Testing"
        ):
            found.append((
                call,
                f"production code asks whether it runs under test ({snippet(call)})",
            ))
    found.extend(marker_reads(source))
    for node in walk(source.root):
        if node.kind() in DECLARING:
            for item in children(node):
                if item.kind() in NAMES and hook_phrase(item.text()):
                    found.append((
                        item,
                        f"production code declares a test-only hook ({item.text()})",
                    ))
    unique = {(line(node), message) for node, message in found}
    return [
        Finding(HOOKS, source.path, f"line {number}: {message}", HOOKS_FIX)
        for number, message in sorted(unique)
    ]


def declared_names(root: SgNode) -> list[tuple[str, int]]:
    found = []
    for node in children(root):
        if node.kind() == "function_declaration":
            name = node.field("name")
            if name is not None and name.text() not in {"main", "init"}:
                found.append((name.text(), line(name)))
        if node.kind() == "type_declaration":
            for spec in children(node):
                name = spec.field("name")
                if spec.kind() == "type_spec" and name is not None:
                    found.append((name.text(), line(name)))
    return found


def definition_starts(root: SgNode) -> frozenset[int]:
    starts = set()
    for node in walk(root):
        if node.kind() in {"function_declaration", "type_spec"}:
            name = node.field("name")
            if name is not None:
                starts.add(name.range().start.index)
    return frozenset(starts)


def used_names(root: SgNode, *, skip_definitions: bool = False) -> set[str]:
    skipped = definition_starts(root) if skip_definitions else frozenset()
    return {
        node.text()
        for node in walk(root)
        if node.kind() in NAMES and node.range().start.index not in skipped
    }


def entry_point_symbols(config: dict[str, Any]) -> set[str]:
    entries = tests_settings(config, "public_entry_points")
    symbols: set[str] = set()
    for value in entries.values() if isinstance(entries, dict) else []:
        items = value if isinstance(value, list) else [value]
        for item in items:
            text = str(item)
            symbols.add(text.rsplit(":", 1)[-1].rsplit(".", 1)[-1].strip())
    return symbols


def test_only_symbols(
    production: dict[str, Source],
    tests: dict[str, Source],
    config: dict[str, Any],
    reported: set[str],
) -> list[Finding]:
    production_uses: set[str] = set()
    defined: list[tuple[str, str, int]] = []
    for path, source in production.items():
        production_uses |= used_names(source.root, skip_definitions=True)
        if path in reported:
            defined.extend(
                (path, name, number) for name, number in declared_names(source.root)
            )
    test_uses: dict[str, str] = {}
    for path, source in sorted(tests.items()):
        for name in used_names(source.root):
            test_uses.setdefault(name, path)
    public = entry_point_symbols(config)
    return [
        Finding(
            HOOKS,
            path,
            f"line {number}: production symbol '{name}' is used only by tests ({test_uses[name]})",
            SYMBOL_FIX,
        )
        for path, name, number in defined
        if name in test_uses and name not in production_uses and name not in public
    ]


def load(repo: Path, path: str) -> Source | None:
    try:
        text = (repo / path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    root = parse(text)
    if has_errors(root):
        return None
    return Source(path, root, imports(root))


def type_names(root: SgNode) -> set[str]:
    specs = [
        spec
        for node in children(root)
        if node.kind() == "type_declaration"
        for spec in children(node)
        if spec.kind() == "type_spec"
    ]
    return {name.text() for spec in specs if (name := spec.field("name")) is not None}


def owned_type_names(sources: dict[str, Source]) -> frozenset[str]:
    return frozenset(
        name for source in sources.values() for name in type_names(source.root)
    )


def lint(repo: Path, config: dict[str, Any], paths: list[str]) -> list[Finding]:
    every = go_files(repo_paths(repo))
    chosen = (
        go_files([item for raw in paths if (item := normalized_path(repo, raw))])
        if paths
        else every
    )
    owned = owned_imports(repo, config)
    production: dict[str, Source] = {}
    tests: dict[str, Source] = {}
    for path in every:
        if is_test_path(path) or owned_path(path, config):
            source = load(repo, path)
            if source is not None:
                (tests if is_test_path(path) else production)[path] = source
    owned_types = owned_type_names(production)
    findings: list[Finding] = []
    reported: set[str] = set()
    for path in chosen:
        if path in tests:
            source = tests[path]
            findings.extend(self_mocking(source, owned, owned_types))
            findings.extend(evasion(source))
            findings.extend(property_seed(source))
        elif path in production:
            findings.extend(hook_findings(production[path]))
            reported.add(path)
    findings.extend(test_only_symbols(production, tests, config, reported))
    return apply_exceptions(findings, config)
