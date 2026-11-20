from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.context.changes import (
    Change,
    DiffError,
    file_at,
    git,
    name_changes,
    parse_range,
    range_finding,
)
from guardrails.context.config.adapter_declarations import tier_locations
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any, settings

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
SHELL = {".sh", ".bash", ".zsh"}
WORD_BREAKS = " \t\n;&|()<>"
HASH = {
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".yml",
    ".yaml",
    ".toml",
    ".conf",
    ".rb",
    ".pl",
    ".pm",
    ".r",
    ".ps1",
    ".dockerfile",
    ".mk",
    ".nix",
    ".jl",
    ".tcl",
    ".awk",
    ".tf",
    ".tfvars",
    ".hcl",
    ".env",
    ".graphql",
    ".gql",
    ".ex",
    ".exs",
    ".cmake",
    ".gemspec",
    ".rake",
}
SLASH = {
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".jsonc",
    ".json5",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hpp",
    ".java",
    ".cs",
    ".go",
    ".rs",
    ".swift",
    ".kt",
    ".kts",
    ".scala",
    ".dart",
    ".php",
    ".css",
    ".scss",
    ".less",
    ".vue",
    ".svelte",
    ".proto",
    ".groovy",
    ".gradle",
    ".sol",
    ".m",
    ".mm",
    ".tf",
    ".tfvars",
    ".hcl",
    ".prisma",
}
HTML = {".html", ".htm", ".xml", ".svg", ".md", ".markdown", ".mdx"}
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


def finding(rule: str, path: str, line: int, message: str) -> Finding:
    return Finding(
        rule,
        path,
        f"line {line}: {message}",
        "implement the behavior without comments, placeholder bodies or product mocks",
    )


HASH_NAMES = frozenset({
    "dockerfile",
    "makefile",
    "cmakelists.txt",
    "rakefile",
    ".gitignore",
    ".gitattributes",
    ".env",
})
SUFFIX_MARKERS: tuple[tuple[frozenset[str], tuple[tuple[str, str], ...]], ...] = (
    (
        frozenset({".sql", ".lua", ".hs", ".elm", ".vhd", ".vhdl", ".ada"}),
        (("--", "\n"),),
    ),
    (frozenset({".sql"}), (("/*", "*/"),)),
    (frozenset({".hs", ".elm"}), (("{-", "-}"),)),
    (frozenset({".tex", ".erl", ".hrl", ".pro"}), (("%", "\n"),)),
    (
        frozenset({".el", ".lisp", ".clj", ".cljs", ".edn", ".scm", ".rkt", ".asm"}),
        ((";", "\n"),),
    ),
    (frozenset({".ini", ".cfg"}), (("#", "\n"), (";", "\n"))),
    (frozenset({".properties"}), (("#", "\n"), ("!", "\n"))),
    (frozenset({*HTML, ".vue", ".svelte"}), (("<!--", "-->"),)),
)


def comment_markers(path: str) -> list[tuple[str, str]]:
    suffix = Path(path).suffix.lower()
    markers: list[tuple[str, str]] = []
    if suffix in HASH or Path(path).name.lower() in HASH_NAMES:
        markers.append(("#", "\n"))
    if suffix in SLASH:
        markers.append(("/*", "*/"))
        if suffix != ".css":
            markers.append(("//", "\n"))
    for suffixes, extra in SUFFIX_MARKERS:
        if suffix in suffixes:
            markers.extend(extra)
    if suffix == ".lua":
        markers.insert(0, ("--[[", "]]"))
    return markers


def comment_stop(text: str, at: int, marker: tuple[str, str]) -> int:
    start, end = marker
    stop = text.find(end, at + len(start))
    return len(text) if stop < 0 else stop + (0 if end == "\n" else len(end))


def comment_span(
    text: str, at: int, stop: int, marker: tuple[str, str]
) -> tuple[int, int, str]:
    start, end = marker
    content_end = stop - len(end) if end != "\n" and text[:stop].endswith(end) else stop
    return (
        text.count("\n", 0, at) + 1,
        text.count("\n", 0, max(at, stop - 1)) + 1,
        text[at + len(start) : content_end],
    )


def string_stop(text: str, at: int) -> int:
    quote = text[at] * (3 if text.startswith(text[at] * 3, at) else 1)
    stop = at + len(quote)
    while stop < len(text):
        if text[stop] == "\\":
            stop += 2
        elif text.startswith(quote, stop):
            stop += len(quote)
            if len(quote) == 1 and text.startswith(quote, stop):
                stop += 1
                continue
            break
        else:
            stop += 1
    return stop


def opens_comment(text: str, at: int, start: str, suffix: str) -> bool:
    return text.startswith(start, at) and not (
        suffix in SHELL and at > 0 and text[at - 1] not in WORD_BREAKS
    )


def parser_directives(text: str, path: str) -> set[int]:
    if (
        Path(path).suffix.lower() != ".dockerfile"
        and Path(path).name.lower() != "dockerfile"
    ):
        return set()
    lines: set[int] = set()
    for number, line in enumerate(text.splitlines(), 1):
        if not PARSER_DIRECTIVE.fullmatch(line):
            break
        lines.add(number)
    return lines


def lex(text: str, path: str) -> tuple[list[tuple[int, int, str]], str]:
    suffix = Path(path).suffix.lower()
    markers = comment_markers(path)
    masked = list(text)
    comments = []
    at = 0
    while at < len(text):
        marker = next(
            (
                (start, end)
                for start, end in markers
                if opens_comment(text, at, start, suffix)
            ),
            None,
        )
        if marker:
            stop = comment_stop(text, at, marker)
            if not (at == 0 and text.startswith("#!")):
                comments.append(comment_span(text, at, stop, marker))
        elif text[at] in "\"'`" and suffix not in HTML:
            stop = string_stop(text, at)
        else:
            at += 1
            continue
        for index in range(at, min(stop, len(text))):
            if masked[index] != "\n":
                masked[index] = " "
        at = stop
    return comments, "".join(masked)


def is_test(path: str, profile: dict[str, Any]) -> bool:
    layout = profile.get("layout", {})
    locations = [
        location for values in tier_locations(profile).values() for location in values
    ]
    locations.extend(as_list(layout.get("support_dir")))
    locations.extend(as_list(layout.get("fixtures_dir")))
    locations.extend(as_list(settings(profile, "check").get("exclude")))
    return matches_any(path, locations)


def add_comment_findings(
    text: str, path: str, added: set[int], findings: list[Finding]
) -> None:
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if (
            token.type == tokenize.COMMENT
            and token.start[0] in added
            and not DIRECTIVE.match(token.string[1:].strip())
            and not (token.start[0] == 1 and token.string.startswith("#!"))
        ):
            findings.append(finding(COMMENTS, path, token.start[0], "added comment"))


def declaration_names(tree: ast.Module) -> dict[str, str]:
    names = {
        "Protocol": "Protocol",
        "abstractmethod": "abstractmethod",
        "overload": "overload",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "typing",
            "typing_extensions",
            "abc",
        }:
            names.update(
                (alias.asname or alias.name, alias.name) for alias in node.names
            )
    return names


def protocol_members(tree: ast.Module, names: dict[str, str]) -> set[int]:
    members: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
            (isinstance(base, ast.Name) and names.get(base.id) == "Protocol")
            or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
            for base in node.bases
        ):
            members.update(id(child) for child in node.body)
    return members


def docstring_findings(tree: ast.Module, path: str, added: set[int]) -> list[Finding]:
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if (
            isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and node.body
        ):
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                findings.extend(
                    finding(COMMENTS, path, line, "added docstring")
                    for line in sorted(
                        added.intersection(
                            range(first.lineno, (first.end_lineno or first.lineno) + 1)
                        )
                    )
                )
    return findings


def placeholder_finding(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    added: set[int],
    names: dict[str, str],
) -> Finding | None:
    decorators = {
        names.get(name, name)
        for item in node.decorator_list
        for name in [ast.unparse(item).split("(")[0].split(".")[-1]]
    }
    body = node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    if len(body) != 1 or decorators.intersection({"abstractmethod", "overload"}):
        return None
    item = body[0]
    placeholder = isinstance(item, ast.Pass) or (
        isinstance(item, ast.Expr)
        and isinstance(item.value, ast.Constant)
        and item.value.value is Ellipsis
    )
    if isinstance(item, ast.Raise):
        exc = item.exc.func if isinstance(item.exc, ast.Call) else item.exc
        placeholder |= isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    if placeholder and added.intersection(
        range(item.lineno, (item.end_lineno or item.lineno) + 1)
    ):
        return finding(PLACEHOLDERS, path, item.lineno, "placeholder function body")
    return None


def mock_import_finding(node: ast.AST, path: str) -> Finding | None:
    names: list[str] = []
    if isinstance(node, ast.Import):
        names = [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        names = [
            node.module or "",
            *(
                (node.module + "." + alias.name) if node.module else alias.name
                for alias in node.names
            ),
        ]
    if any(
        name == module or name.startswith(module + ".")
        for name in names
        for module in MOCK_MODULES
    ):
        return finding(
            MOCKS,
            path,
            getattr(node, "lineno", 1),
            "mock library imported in product code",
        )
    return None


def python_findings(
    text: str, path: str, added: set[int], profile: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []
    try:
        add_comment_findings(text, path, added, findings)
        tree = ast.parse(text, filename=path)
    except (SyntaxError, tokenize.TokenError, IndentationError) as error:
        return [
            *findings,
            finding(
                COMMENTS,
                path,
                getattr(error, "lineno", 1) or 1,
                f"cannot tokenize/parse Python: {error}",
            ),
        ]
    names = declaration_names(tree)
    protocol_methods = protocol_members(tree, names)
    findings.extend(docstring_findings(tree, path, added))
    product = not is_test(path, profile)
    for node in ast.walk(tree):
        lines = range(
            getattr(node, "lineno", 0), (getattr(node, "end_lineno", 0) or 0) + 1
        )
        if not added.intersection(lines):
            continue
        found = []
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not path.endswith(".pyi")
            and id(node) not in protocol_methods
        ):
            found.append(placeholder_finding(node, path, added, names))
        if product:
            found.append(mock_import_finding(node, path))
        findings.extend(item for item in found if item is not None)
    return findings


def inspect(
    text: str, path: str, added: set[int], profile: dict[str, Any]
) -> list[Finding]:
    if not added:
        return []
    if path.endswith((".py", ".pyi")):
        return python_findings(text, path, added, profile)
    comments, code = lex(text, path)
    findings = comment_findings(comments, path, added - parser_directives(text, path))
    if not is_test(path, profile):
        findings.extend(script_mock_findings(text, code, path, added))
    return findings


def comment_findings(
    comments: list[tuple[int, int, str]], path: str, added: set[int]
) -> list[Finding]:
    findings = []
    for start, end, content in comments:
        lines = [line.strip().lstrip("*").strip() for line in content.split("\n")]
        has_directive = any(DIRECTIVE.match(line) for line in lines)
        for line in sorted(added.intersection(range(start, end + 1))):
            value = lines[line - start]
            if not DIRECTIVE.match(value) and (value or not has_directive):
                findings.append(finding(COMMENTS, path, line, "added comment"))
    return findings


def script_mock_findings(
    text: str, code: str, path: str, added: set[int]
) -> list[Finding]:
    findings = []
    pattern = re.compile(
        r"\b(?:jest|vi)\s*\.\s*(?:mock|doMock|unstable_mockModule)\s*\("
    )
    for match in pattern.finditer(code):
        line = code.count("\n", 0, match.start()) + 1
        if line in added:
            findings.append(finding(MOCKS, path, line, "mock call in product code"))
    import_pattern = re.compile(
        r"(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*(?:\(\s*)?)[\"']("
        + "|".join(re.escape(name) for name in MOCK_MODULES)
        + r")(?:/[^\"']*)?[\"']"
    )
    for match in import_pattern.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        if line in added and code[match.start() : match.start() + 1].strip():
            findings.append(
                finding(MOCKS, path, line, "mock library imported in product code")
            )
    return findings


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        diff = parse_range(argv)
        changes = name_changes(repo, diff.base, diff.head)
        return check_files(repo, profile, changes, Revisions(diff.base, diff.head))
    except DiffError as error:
        return [range_finding(COMMENTS, error)]


@dataclass(frozen=True)
class Revisions:
    base: str
    head: str | None = None
    staged: bool = False


def check_files(
    repo: Path, profile: dict[str, Any], changes: list[Change], revisions: Revisions
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
        findings.extend(inspect(text, path, added, profile))
    return findings


def commit_findings(repo: Path, profile: dict[str, Any]) -> list[Finding]:
    try:
        try:
            base = git(repo, "rev-parse", "--verify", "HEAD").strip()
        except DiffError:
            base = git(repo, "hash-object", "-t", "tree", "/dev/null").strip()
        staged = name_changes(repo, "--cached", base)
        untracked = git(repo, "ls-files", "-z", "--others", "--exclude-standard")
        changes = [
            *name_changes(repo, base),
            *(Change("A", path) for path in untracked.split("\0") if path),
        ]
        findings = check_files(repo, profile, staged, Revisions(base, staged=True))
        findings.extend(
            check_files(
                repo,
                profile,
                [change for change in changes if (repo / change.path).is_file()],
                Revisions(base),
            )
        )
        return list(dict.fromkeys(findings))
    except (DiffError, OSError) as error:
        return [range_finding(COMMENTS, DiffError(str(error)))]
