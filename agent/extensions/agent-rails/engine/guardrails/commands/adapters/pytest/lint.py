from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters.marker_bindings import inline_bindings
from guardrails.commands.adapters.pytest.dialect import NAME
from guardrails.commands.adapters.pytest.symbol_usage import test_only_findings
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.reader import tests_settings
from guardrails.context.config.suite_files import Suite, adapter_suite, in_suite
from guardrails.foundation.findings import Finding, Remediation, remediation

if TYPE_CHECKING:
    from ast_grep_py import Config, SgNode

PATTERNS_FILE = "pytest-patterns.json"
INLINED_PATTERNS = frozenset({"no-test-hooks-environment"})
SKIPPED_PARTS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "build",
    "dist",
    "__pycache__",
})


@dataclass(frozen=True)
class Pattern:
    name: str
    rule_id: str
    scope: str
    target: str | None
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
    patterns = []
    for data in json.loads(text):
        options: Config = {"rule": data["rule"]}
        if "constraints" in data:
            options["constraints"] = data["constraints"]
        if "utils" in data:
            options["utils"] = data["utils"]
        patterns.append(
            Pattern(
                data["name"],
                data["id"],
                data["scope"],
                data.get("target"),
                data["message"],
                remediation(data["remediation"]),
                options,
            )
        )
    return patterns


def owned_packages(config: dict[str, Any]) -> frozenset[str]:
    entries = tests_settings(config, "public_entry_points")
    names = set()
    for key in entries if isinstance(entries, dict) else {}:
        parts = [part for part in str(key).replace("/", ".").split(".") if part]
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts:
            names.add(parts[0])
    return frozenset(names)


def is_pytest_test_file(path: str, suite: Suite) -> bool:
    return PurePosixPath(path).name == "conftest.py" or in_suite(path, suite)


def python_files(repo: Path, argv: list[str]) -> list[str]:
    if argv:
        return sorted({path for path in argv if path.endswith(".py")})
    from guardrails.foundation.inventory import InventoryError, repo_paths

    try:
        paths = repo_paths(repo)
    except InventoryError:
        paths = [path.relative_to(repo).as_posix() for path in repo.rglob("*.py")]
    return sorted(
        path
        for path in paths
        if path.endswith(".py") and not SKIPPED_PARTS & set(PurePosixPath(path).parts)
    )


def import_map(source: str) -> dict[str, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    names[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def string_value(text: str) -> str | None:
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def resolve(node: SgNode, imports: dict[str, str]) -> str | None:
    kind = node.kind()
    text = node.text()
    if kind == "string":
        return string_value(text)
    if kind not in {"identifier", "attribute"}:
        return None
    head, _, rest = text.partition(".")
    origin = imports.get(head)
    if origin is None:
        return None
    return f"{origin}.{rest}" if rest else origin


def arguments(call: SgNode) -> list[SgNode]:
    node = call.field("arguments")
    if node is None:
        return []
    return [
        child
        for child in node.children()
        if child.is_named() and child.kind() != "comment"
    ]


def target_nodes(match: SgNode, target: str) -> list[SgNode]:
    if target == "assigned-attribute-object":
        left = match.field("left")
        value = left.field("object") if left is not None else None
        return [value] if value is not None else []
    positional = [
        item for item in arguments(match) if item.kind() != "keyword_argument"
    ]
    if target == "first-argument":
        return positional[:1]
    if target.startswith("keyword:"):
        wanted = set(target.split(":", 1)[1].split(","))
        found: list[SgNode] = []
        for item in arguments(match):
            name = item.field("name")
            if (
                item.kind() == "keyword_argument"
                and name is not None
                and name.text() in wanted
            ):
                value = item.field("value")
                if value is not None:
                    found.append(value)
        return found
    raise ValueError(f"unknown rule target {target!r}")


def owned_target(
    match: SgNode, target: str, imports: dict[str, str], owned: frozenset[str]
) -> str | None:
    for node in target_nodes(match, target):
        origin = resolve(node, imports)
        if origin and origin.split(".")[0] in owned:
            return origin
    return None


def file_findings(
    path: str, source: str, patterns: list[Pattern], owned: frozenset[str]
) -> list[Finding]:
    from ast_grep_py import SgRoot

    roots = {
        False: SgRoot(source, "python").root(),
        True: SgRoot(inline_bindings(source, "python"), "python").root(),
    }
    imports = import_map(source)
    findings: list[Finding] = []
    for pattern in patterns:
        root = roots[pattern.name in INLINED_PATTERNS]
        for match in root.find_all(pattern.config):
            target = None
            if pattern.target:
                target = owned_target(match, pattern.target, imports, owned)
                if target is None:
                    continue
            line = match.range().start.line + 1
            snippet = " ".join(match.text().split())[:80]
            message = pattern.message.format(target=target, match=snippet)
            findings.append(
                Finding(
                    pattern.rule_id,
                    path,
                    f"line {line}: {message}",
                    pattern.remediation.render(),
                )
            )
    return findings


def lint(repo: Path, config: dict[str, Any], paths: list[str]) -> list[Finding]:
    owned = owned_packages(config)
    suite = adapter_suite(config, NAME)
    patterns = load_patterns()
    findings: list[Finding] = []
    production: set[str] = set()
    for path in python_files(repo, paths):
        if is_pytest_test_file(path, suite):
            scoped = [item for item in patterns if item.scope == "tests"]
        elif is_owned(path, owned):
            scoped = [item for item in patterns if item.scope == "production"]
            production.add(path)
        else:
            continue
        source = read_source(repo, path)
        if source is None:
            continue
        findings.extend(file_findings(path, source, scoped, owned))
    findings.extend(test_only_symbol_findings(repo, config, production))
    return apply_exceptions(findings, config)


def is_owned(path: str, owned: frozenset[str]) -> bool:
    return bool(owned & {*PurePosixPath(path).parts[:-1], PurePosixPath(path).stem})


def read_source(repo: Path, path: str) -> str | None:
    try:
        return (repo / path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def test_only_symbol_findings(
    repo: Path, config: dict[str, Any], reported: set[str]
) -> list[Finding]:
    if not reported:
        return []
    owned = owned_packages(config)
    suite = adapter_suite(config, NAME)
    production: dict[str, str] = {}
    tests: dict[str, str] = {}
    for path in python_files(repo, []):
        if is_pytest_test_file(path, suite):
            target = tests
        elif is_owned(path, owned):
            target = production
        else:
            continue
        source = read_source(repo, path)
        if source is not None:
            target[path] = source
    return test_only_findings(production, tests, config, reported)
