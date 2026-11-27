from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from guardrails.context.changes import (
    Change,
    DiffError,
    commit_base,
    file_at,
    git,
    name_changes,
    parse_range,
    range_finding,
)
from guardrails.context.config.reader import checker_settings
from guardrails.context.config.suite_files import is_test_source
from guardrails.foundation import conventions
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any
from guardrails.foundation.source import comments, modules, python, tree, typescript

if TYPE_CHECKING:
    from ast_grep_py import SgNode

COMMENTS = "CODE.no-comments"
PLACEHOLDERS = "CODE.no-placeholder-bodies"
MOCKS = "CODE.no-product-mocks"
DIRECTIVE = re.compile(
    r"^(?:noqa\b|type:\s*ignore\b|pyright:|fmt:\s*(?:off|on)\b|ruff:|isort:|"
    r"pylint:|eslint-|@ts-(?:expect-error|ignore|nocheck)\b|prettier-ignore\b|"
    r"biome-ignore\b|go:(?:build|embed|generate)\b|nolint\b|SPDX-License-Identifier:|"
    r"markdownlint-|/\s*<reference\s|shellcheck\s+(?:source|shell)=|"
    r"yaml-language-server:\s*\$schema=|"
    r".*(?:-\*-.*-\*-|vim:|vi:|ex:)|.*coding[:=]\s*[-\w.]+)"
)
PARSER_DIRECTIVE = re.compile(r"#\s*(?:syntax|escape)\s*=\s*\S+\s*", re.IGNORECASE)
DECLARERS = frozenset({"typing", "typing_extensions", "abc"})
CLASSES = frozenset({"class_definition"})
FUNCTIONS = frozenset({"function_definition"})
SCRIPT_CALLS = frozenset({"call_expression"})
MOCK_MODULES = {
    "mock",
    "pytest_mock",
    "unittest.mock",
    "sinon",
    "jest-mock",
    "ts-mockito",
    "testdouble",
    "mock-require",
    "proxyquire",
}


def mock_calls() -> frozenset[str]:
    """The JavaScript runners' mock APIs called through their namespace (jest.mock,
    vi.spyOn, mock.module): every dotted name the runner vocabularies list. A bare name
    (bun's mock) is an imported binding, so it is not read."""
    apis = conventions.data("javascript-patterns.json")["mock_apis"]
    return frozenset(name for calls in apis.values() for name in calls if "." in name)


MOCK_CALLS = mock_calls()


def finding(rule: str, path: str, line: int, message: str) -> Finding:
    return Finding(
        rule,
        path,
        f"line {line}: {message}",
        "implement the behavior without comments, placeholder bodies or product mocks",
    )


def parser_directives(text: str, path: str) -> set[int]:
    if not conventions.parser_directive_file(path):
        return set()
    lines: set[int] = set()
    for number, line in enumerate(text.splitlines(), 1):
        if not PARSER_DIRECTIVE.fullmatch(line):
            break
        lines.add(number)
    return lines


def product_source(path: str, config: dict[str, Any]) -> bool:
    """Whether a file is product code the mock checks read: not test source and not a
    path the check config excludes."""
    excluded = as_list(checker_settings(config, "check").get("exclude"))
    return not (is_test_source(path, config) or matches_any(path, excluded))


def added_lines(node: SgNode, added: set[int]) -> list[int]:
    return sorted(added.intersection(python.statement_lines(node)))


def declaration_names(found: list[python.PythonImport]) -> dict[str, str]:
    names = {
        "Protocol": "Protocol",
        "abstractmethod": "abstractmethod",
        "overload": "overload",
    }
    for item in found:
        if item.statement == "from" and not item.level and item.module in DECLARERS:
            names.update(zip(item.bindings, item.names, strict=True))
    return names


def protocol_members(root: SgNode, names: dict[str, str]) -> set[int]:
    members: set[int] = set()
    for node in tree.of_kinds(root, CLASSES):
        bases = node.field("superclasses")
        if bases is not None and any(
            (base.kind() == "identifier" and names.get(base.text()) == "Protocol")
            or (
                base.kind() == "attribute"
                and (attribute := base.field("attribute")) is not None
                and attribute.text() == "Protocol"
            )
            for base in tree.named(bases)
        ):
            members.update(
                start(member) for member in python.members(node.field("body"))
            )
    return members


def start(node: SgNode) -> int:
    return node.range().start.index


def docstring_findings(root: SgNode, path: str, added: set[int]) -> list[Finding]:
    return [
        finding(COMMENTS, path, number, "added docstring")
        for statement in python.docstrings(root)
        for number in added_lines(statement, added)
    ]


def placeholder_statement(item: SgNode) -> bool:
    kind = item.kind()
    if kind == "pass_statement":
        return True
    values = tree.named(item)
    if kind == "expression_statement":
        return len(values) == 1 and values[0].kind() == "ellipsis"
    if kind != "raise_statement" or not values:
        return False
    raised = values[0]
    if raised.kind() == "call":
        raised = raised.field("function") or raised
    return raised.kind() == "identifier" and raised.text() == "NotImplementedError"


def placeholder_finding(
    node: SgNode, path: str, added: set[int], names: dict[str, str]
) -> Finding | None:
    decorators = {
        names.get(name, name)
        for text in python.decorators(node)
        for name in [text.split("(")[0].split(".")[-1].strip()]
    }
    body = node.field("body")
    statements = [] if body is None else tree.named(body)
    if body is not None and python.docstring(body) is not None:
        statements = statements[1:]
    if len(statements) != 1 or decorators.intersection({"abstractmethod", "overload"}):
        return None
    item = statements[0]
    lines = added_lines(item, added)
    if placeholder_statement(item) and lines:
        return finding(PLACEHOLDERS, path, tree.line(item), "placeholder function body")
    return None


def mock_import_finding(item: python.PythonImport, path: str) -> Finding | None:
    if item.level:
        return None
    names = [item.module]
    if item.statement == "from":
        names.extend(f"{item.module}.{name}" for name in item.names)
    if any(
        name == module or name.startswith(module + ".")
        for name in names
        for module in MOCK_MODULES
    ):
        return finding(MOCKS, path, item.line, "mock library imported in product code")
    return None


def python_findings(
    text: str, path: str, added: set[int], config: dict[str, Any]
) -> list[Finding]:
    try:
        root = tree.parse(path, text)
    except tree.SourceError as error:
        return [finding(COMMENTS, path, error.line or 1, str(error))]
    found_imports = python.imports(root)
    names = declaration_names(found_imports)
    protocol_methods = protocol_members(root, names)
    findings = [
        *comment_findings(tree_comments(root, path), text, path, added),
        *docstring_findings(root, path, added),
    ]
    if not path.endswith(modules.stub_extension()):
        for node in tree.of_kinds(root, FUNCTIONS):
            if start(node) in protocol_methods:
                continue
            placeholder = placeholder_finding(node, path, added, names)
            if placeholder is not None:
                findings.append(placeholder)
    if product_source(path, config):
        ends: dict[int, int] = {}
        for node in tree.of_kinds(root, python.IMPORT_KINDS):
            ends[tree.line(node)] = max(
                ends.get(tree.line(node), 0), tree.end_line(node)
            )
        findings.extend(
            mock
            for item in found_imports
            if added.intersection(range(item.line, ends[item.line] + 1))
            and (mock := mock_import_finding(item, path)) is not None
        )
    return findings


def tree_comments(root: SgNode, path: str) -> list[comments.Comment]:
    return comments.tree_comments(root, conventions.comment_syntax(path))


def inspect(
    text: str, path: str, added: set[int], config: dict[str, Any]
) -> list[Finding]:
    if not added:
        return []
    language = tree.language_of(path)
    if language == "python":
        return python_findings(text, path, added, config)
    if language is not None:
        return script_findings(text, path, added, config)
    found = comments.lexed(text, conventions.comment_syntax(path))
    return comment_findings(found, text, path, added - parser_directives(text, path))


def comment_findings(
    found: list[comments.Comment], text: str, path: str, added: set[int]
) -> list[Finding]:
    findings = []
    for comment in found:
        if comment.line == 1 and text.startswith("#!"):
            continue
        lines = [line.strip().lstrip("*").strip() for line in comment.text.split("\n")]
        has_directive = any(DIRECTIVE.match(line) for line in lines)
        for line in sorted(
            added.intersection(range(comment.line, comment.end_line + 1))
        ):
            value = lines[line - comment.line]
            if not DIRECTIVE.match(value) and (value or not has_directive):
                findings.append(finding(COMMENTS, path, line, "added comment"))
    return findings


def script_findings(
    text: str, path: str, added: set[int], config: dict[str, Any]
) -> list[Finding]:
    """TypeScript, JavaScript and Go: the comments their grammar reads and, in
    TypeScript and JavaScript product code, mock imports and mock API calls."""
    try:
        root = tree.parse(path, text)
    except tree.SourceError as error:
        return [finding(COMMENTS, path, error.line or 1, str(error))]
    findings = comment_findings(tree_comments(root, path), text, path, added)
    if tree.language_of(path) == "typescript" and product_source(path, config):
        findings.extend(script_mock_findings(root, path, added))
    return findings


def mocked(specifier: str) -> bool:
    return any(
        specifier == module or specifier.startswith(module + "/")
        for module in MOCK_MODULES
    )


def mock_call(node: SgNode) -> bool:
    function = node.field("function")
    return function is not None and "".join(function.text().split()) in MOCK_CALLS


def script_mock_findings(root: SgNode, path: str, added: set[int]) -> list[Finding]:
    findings = [
        finding(MOCKS, path, tree.line(node), "mock call in product code")
        for node in tree.of_kinds(root, SCRIPT_CALLS)
        if tree.line(node) in added and mock_call(node)
    ]
    ends: dict[int, int] = {}
    for node in tree.of_kinds(root, typescript.IMPORT_KINDS):
        ends[tree.line(node)] = max(ends.get(tree.line(node), 0), tree.end_line(node))
    findings.extend(
        finding(MOCKS, path, item.line, "mock library imported in product code")
        for item in typescript.imports(root)
        if mocked(item.specifier)
        and added.intersection(range(item.line, ends[item.line] + 1))
    )
    return findings


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        diff = parse_range(argv)
        changes = name_changes(repo, diff.base, diff.head)
        return check_files(repo, config, changes, Revisions(diff.base, diff.head))
    except DiffError as error:
        return [range_finding(COMMENTS, error)]


@dataclass(frozen=True)
class Revisions:
    base: str
    head: str | None = None
    staged: bool = False


def check_files(
    repo: Path, config: dict[str, Any], changes: list[Change], revisions: Revisions
) -> list[Finding]:
    findings = []
    base, head, staged = revisions.base, revisions.head, revisions.staged
    for change in changes:
        if change.status[0] not in "ACMR":
            continue
        path = change.path
        text = (
            git(repo, "show", f":{path}")
            if staged
            else file_at(repo, head, path)
            if head
            else (repo / path).read_text(errors="replace")
        )
        if text is None or "\0" in text:
            continue
        args = [
            "diff",
            "-U0",
            "-M",
            "--no-ext-diff",
            *(["--cached"] if staged else []),
            base,
            *([head] if head else []),
            "--",
            *change.paths,
        ]
        patch = git(repo, *args)
        added: set[int] = set()
        for line in patch.splitlines():
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                start, count = int(match[1]), int(match[2] or 1)
                added.update(range(start, start + count))
        if not patch and file_at(repo, base, change.old_path or path) is None:
            added.update(range(1, len(text.splitlines()) + 1))
        findings.extend(inspect(text, path, added, config))
    return findings


def commit_findings(repo: Path, config: dict[str, Any]) -> list[Finding]:
    try:
        base = commit_base(repo)
        staged = name_changes(repo, "--cached", base)
        untracked = git(repo, "ls-files", "-z", "--others", "--exclude-standard")
        changes = [
            *name_changes(repo, base),
            *(Change("A", path) for path in untracked.split("\0") if path),
        ]
        findings = check_files(repo, config, staged, Revisions(base, staged=True))
        findings.extend(
            check_files(
                repo,
                config,
                [change for change in changes if (repo / change.path).is_file()],
                Revisions(base),
            )
        )
        return list(dict.fromkeys(findings))
    except (DiffError, OSError) as error:
        return [range_finding(COMMENTS, DiffError(str(error)))]
