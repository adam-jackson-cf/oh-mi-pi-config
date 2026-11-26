from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.commands.adapters import hook_vocabulary, owned_source
from guardrails.commands.adapters.marker_bindings import inline_bindings
from guardrails.commands.adapters.pytest.dialect import NAME
from guardrails.commands.adapters.pytest.symbol_usage import (
    ScriptManifests,
    owner,
    test_only_findings,
)
from guardrails.commands.adapters.pytest.syntax import import_map, string_value
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.suite_files import Suite, adapter_suite, in_suite
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding, Remediation, remediation
from guardrails.foundation.source.tree import tolerant_tree

if TYPE_CHECKING:
    from guardrails.foundation.source.tree import Config, SgNode

PATTERNS_FILE = "pytest-patterns.json"
PYPROJECT = "pyproject.toml"
# A pattern's filter: a check its matched node's text must also pass.
FILTERS = {"hook-phrase": hook_vocabulary.hook_phrase}


@dataclass(frozen=True)
class Pattern:
    name: str
    rule_id: str
    scope: str
    target: str | None
    message: str
    remediation: Remediation
    config: Config
    inline: bool = False
    filter: str | None = None


def load_patterns() -> list[Pattern]:
    patterns = []
    for data in conventions.data(PATTERNS_FILE):
        if data.get("filter", None) not in {None, *FILTERS}:
            raise ValueError(f"{PATTERNS_FILE}: unknown filter {data['filter']!r}")
        options: Config = {"rule": hook_vocabulary.expanded(data["rule"])}
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
                data.get("inline_bindings", False),
                data.get("filter"),
            )
        )
    return patterns


def is_pytest_test_file(path: str, suite: Suite) -> bool:
    return conventions.test_support_file(path) or in_suite(path, suite)


def python_files(repo: Path, argv: list[str]) -> list[str]:
    """The Python files that run (a stub holds no hooks, markers or test-only code)."""
    runs = conventions.runtime_extensions("python")
    if argv:
        return sorted({path for path in argv if path.endswith(runs)})
    return sorted(
        path for path in owned_source.repository_paths(repo) if path.endswith(runs)
    )


def pyproject_files(repo: Path) -> list[str]:
    return sorted(
        path
        for path in owned_source.repository_paths(repo)
        if PurePosixPath(path).name == PYPROJECT
    )


def resolve(node: SgNode, imports: dict[str, str]) -> str | None:
    kind = node.kind()
    text = node.text()
    if kind == "string":
        return string_value(node)
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


def in_owned(name: str, owned: frozenset[str]) -> bool:
    return any(name == package or name.startswith(f"{package}.") for package in owned)


def owned_target(
    match: SgNode, target: str, imports: dict[str, str], owned: frozenset[str]
) -> str | None:
    for node in target_nodes(match, target):
        origin = resolve(node, imports)
        if origin and in_owned(origin, owned):
            return origin
    return None


def file_findings(
    source: owned_source.Source, patterns: list[Pattern], owned: frozenset[str]
) -> list[Finding]:
    path = source.path
    roots = {
        False: source.root,
        True: tolerant_tree(
            inline_bindings(source.text, source.root, "python"), "python"
        ),
    }
    imports = import_map(source.root)
    findings: list[Finding] = []
    for pattern in patterns:
        root = roots[pattern.inline]
        for match in root.find_all(pattern.config):
            if pattern.filter is not None and not FILTERS[pattern.filter](match.text()):
                continue
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
    points = owned_source.configured_entry_points(config)
    if isinstance(points, Finding):
        return [points]
    suite = adapter_suite(config, NAME)
    every = python_files(repo, [])
    owned = owner(points, every)

    def is_test(path: str) -> bool:
        return is_pytest_test_file(path, suite)

    selected = set(python_files(repo, paths)) if paths else set(every)
    read = [path for path in every if is_test(path) or owned.owns(path)]
    sources, unreadable = owned_source.load_all(repo, read, is_test)
    findings = [item for item in unreadable if item.path in selected]
    patterns = load_patterns()
    production: set[str] = set()
    for path in sorted(selected & sources.keys()):
        scope = "tests" if is_test(path) else "production"
        if scope == "production":
            production.add(path)
        scoped = [item for item in patterns if item.scope == scope]
        findings.extend(file_findings(sources[path], scoped, owned.packages))
    if production:
        manifests = pyproject_files(repo)
        findings.extend(
            test_only_findings(
                sources,
                is_test,
                production,
                owned.index,
                ScriptManifests(repo, manifests, points),
            )
        )
    return apply_exceptions(findings, config)
