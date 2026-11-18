from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

from omp_guardrails.diffs.changes import DiffError, file_at, git, parse_range, range_finding
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, matches_any
from omp_guardrails.structure.paths import tier_locations

COMMENTS = "CODE.no-comments"
PLACEHOLDERS = "CODE.no-placeholder-bodies"
MOCKS = "CODE.no-product-mocks"
DIRECTIVE = re.compile(
    r"^(?:noqa\b|type:\s*ignore\b|pyright:|fmt:\s*(?:off|on)\b|ruff:|isort:|"
    r"pylint:|eslint-|@ts-(?:expect-error|ignore|nocheck)\b|prettier-ignore\b|"
    r"biome-ignore\b|go:build\b|nolint\b|SPDX-License-Identifier:|markdownlint-|"
    r".*(?:-\*-.*-\*-|vim:|vi:|ex:)|.*coding[:=]\s*[-\w.]+)"
)
HASH = {".sh", ".bash", ".zsh", ".fish", ".yml", ".yaml", ".toml", ".conf", ".rb", ".pl", ".pm", ".r", ".ps1", ".dockerfile", ".mk", ".nix", ".jl", ".tcl", ".awk", ".tf", ".tfvars", ".hcl", ".env", ".graphql", ".gql", ".ex", ".exs", ".cmake", ".gemspec", ".rake"}
SLASH = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts", ".jsonc", ".json5", ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".java", ".cs", ".go", ".rs", ".swift", ".kt", ".kts", ".scala", ".dart", ".php", ".css", ".scss", ".less", ".vue", ".svelte", ".proto", ".groovy", ".gradle", ".sol", ".m", ".mm", ".tf", ".tfvars", ".hcl", ".prisma"}
HTML = {".html", ".htm", ".xml", ".svg", ".md", ".markdown", ".mdx"}
MOCK_MODULES = {"mock", "pytest_mock", "unittest.mock", "sinon", "jest-mock", "ts-mockito", "testdouble", "mock-require", "proxyquire"}


def finding(rule: str, path: str, line: int, message: str) -> Finding:
    return Finding(rule, path, f"line {line}: {message}", "implement the behavior without comments, placeholder bodies or product mocks")


def lex(text: str, path: str) -> tuple[list[tuple[int, int, str]], str]:
    suffix = Path(path).suffix.lower()
    markers: list[tuple[str, str]] = []
    if suffix in HASH or Path(path).name.lower() in {"dockerfile", "makefile", "cmakelists.txt", "rakefile", ".gitignore", ".gitattributes", ".env"}:
        markers.append(("#", "\n"))
    if suffix in SLASH:
        markers.append(("/*", "*/"))
        if suffix != ".css":
            markers.append(("//", "\n"))
    if suffix in {".sql", ".lua", ".hs", ".elm", ".vhd", ".vhdl", ".ada"}:
        markers.append(("--", "\n"))
    if suffix == ".sql":
        markers.append(("/*", "*/"))
    if suffix in {".hs", ".elm"}:
        markers.append(("{-", "-}"))
    if suffix in {".tex", ".erl", ".hrl", ".pro"}:
        markers.append(("%", "\n"))
    if suffix in {".el", ".lisp", ".clj", ".cljs", ".edn", ".scm", ".rkt", ".asm"}:
        markers.append((";", "\n"))
    if suffix == ".lua":
        markers.insert(0, ("--[[", "]]"))
    if suffix in {".ini", ".cfg"}:
        markers.extend([("#", "\n"), (";", "\n")])
    if suffix == ".properties":
        markers.extend([("#", "\n"), ("!", "\n")])
    if suffix in HTML or suffix in {".vue", ".svelte"}:
        markers.append(("<!--", "-->"))
    masked = list(text)
    comments = []
    at = 0
    while at < len(text):
        marker = next(((start, end) for start, end in markers if text.startswith(start, at)), None)
        if marker:
            start, end = marker
            stop = text.find(end, at + len(start))
            stop = len(text) if stop < 0 else stop + (0 if end == "\n" else len(end))
            if not (at == 0 and text.startswith("#!")):
                content_end = stop - len(end) if end != "\n" and text[:stop].endswith(end) else stop
                comments.append((
                    text.count("\n", 0, at) + 1,
                    text.count("\n", 0, max(at, stop - 1)) + 1,
                    text[at + len(start):content_end].strip(),
                ))
        elif text[at] in "\"'`" and suffix not in HTML:
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
        else:
            at += 1
            continue
        for index in range(at, min(stop, len(text))):
            if masked[index] != "\n":
                masked[index] = " "
        at = stop
    return comments, "".join(masked)


def is_test(path: str, profile: dict) -> bool:
    layout = profile.get("layout", {})
    locations = [location for values in tier_locations(profile).values() for location in values]
    locations.extend(as_list(layout.get("support_dir")))
    locations.extend(as_list(layout.get("fixtures_dir")))
    return matches_any(path, locations)


def python_findings(text: str, path: str, added: set[int], profile: dict) -> list[Finding]:
    findings = []
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        for token in tokens:
            if token.type == tokenize.COMMENT and token.start[0] in added and not DIRECTIVE.match(token.string[1:].strip()) and not (token.start[0] == 1 and token.string.startswith("#!")):
                findings.append(finding(COMMENTS, path, token.start[0], "added comment"))
        tree = ast.parse(text, filename=path)
    except (SyntaxError, tokenize.TokenError, IndentationError) as error:
        return [*findings, finding(COMMENTS, path, getattr(error, "lineno", 1) or 1, f"cannot tokenize/parse Python: {error}")]
    protocol_methods = set()
    declaration_names = {"Protocol": "Protocol", "abstractmethod": "abstractmethod", "overload": "overload"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"typing", "typing_extensions", "abc"}:
            declaration_names.update((alias.asname or alias.name, alias.name) for alias in node.names)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any((isinstance(base, ast.Name) and declaration_names.get(base.id) == "Protocol") or (isinstance(base, ast.Attribute) and base.attr == "Protocol") for base in node.bases):
            protocol_methods.update(id(child) for child in node.body)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                for line in sorted(added.intersection(range(first.lineno, (first.end_lineno or first.lineno) + 1))):
                    findings.append(finding(COMMENTS, path, line, "added docstring"))
    for node in ast.walk(tree):
        lines = range(getattr(node, "lineno", 0), (getattr(node, "end_lineno", 0) or 0) + 1)
        if not added.intersection(lines):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not path.endswith(".pyi") and id(node) not in protocol_methods:
            decorators = {declaration_names.get(name, name) for item in node.decorator_list for name in [ast.unparse(item).split("(")[0].split(".")[-1]]}
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                body = body[1:]
            if len(body) == 1 and not decorators.intersection({"abstractmethod", "overload"}):
                item = body[0]
                placeholder = isinstance(item, ast.Pass) or (isinstance(item, ast.Expr) and isinstance(item.value, ast.Constant) and item.value.value is Ellipsis)
                if isinstance(item, ast.Raise):
                    exc = item.exc.func if isinstance(item.exc, ast.Call) else item.exc
                    placeholder |= isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
                if placeholder and added.intersection(range(item.lineno, (item.end_lineno or item.lineno) + 1)):
                    findings.append(finding(PLACEHOLDERS, path, item.lineno, "placeholder function body"))
        if not is_test(path, profile):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or "", *((node.module + "." + alias.name) if node.module else alias.name for alias in node.names)]
            if any(name == module or name.startswith(module + ".") for name in names for module in MOCK_MODULES):
                findings.append(finding(MOCKS, path, getattr(node, "lineno", 1), "mock library imported in product code"))
    return findings


def inspect(text: str, path: str, added: set[int], profile: dict) -> list[Finding]:
    if not added:
        return []
    if path.endswith((".py", ".pyi")):
        return python_findings(text, path, added, profile)
    comments, code = lex(text, path)
    findings = [finding(COMMENTS, path, line, "added comment") for start, end, content in comments if not DIRECTIVE.match(content) for line in sorted(added.intersection(range(start, end + 1)))]
    if not is_test(path, profile):
        pattern = re.compile(r"\b(?:jest|vi)\s*\.\s*(?:mock|doMock|unstable_mockModule)\s*\(")
        for match in pattern.finditer(code):
            line = code.count("\n", 0, match.start()) + 1
            if line in added:
                findings.append(finding(MOCKS, path, line, "mock call in product code"))
        import_pattern = re.compile(r"(?:\bfrom\s*|\brequire\s*\(\s*|\bimport\s*(?:\(\s*)?)[\"'](" + "|".join(re.escape(name) for name in MOCK_MODULES) + r")(?:/[^\"']*)?[\"']")
        for match in import_pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            if line in added and code[match.start():match.start() + 1].strip():
                findings.append(finding(MOCKS, path, line, "mock library imported in product code"))
    return findings


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    try:
        diff = parse_range(argv)
        names = git(repo, "diff", "--name-only", "-z", "--diff-filter=ACMR", diff.base, diff.head, "--").split("\0")
        return check_files(repo, profile, names, diff.base, diff.head)
    except DiffError as error:
        return [range_finding(COMMENTS, error)]


def check_files(repo: Path, profile: dict, names: list[str], base: str, head: str | None, *, staged: bool = False) -> list[Finding]:
    findings = []
    for path in filter(None, names):
        text = git(repo, "show", f":{path}") if staged else file_at(repo, head, path) if head else (repo / path).read_text(errors="replace")
        if text is None or "\0" in text:
            continue
        args = ["diff", "-U0", "--no-ext-diff", *(["--cached"] if staged else []), base, *([head] if head else []), "--", path]
        patch = git(repo, *args)
        added = set()
        for line in patch.splitlines():
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if match:
                start, count = int(match[1]), int(match[2] or 1)
                added.update(range(start, start + count))
        if not patch and file_at(repo, base, path) is None:
            added.update(range(1, len(text.splitlines()) + 1))
        findings.extend(inspect(text, path, added, profile))
    return findings


def commit_findings(repo: Path, profile: dict) -> list[Finding]:
    try:
        try:
            base = git(repo, "rev-parse", "--verify", "HEAD").strip()
        except DiffError:
            base = git(repo, "hash-object", "-t", "tree", "/dev/null").strip()
        staged = git(repo, "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", base, "--").split("\0")
        names = (git(repo, "diff", "--name-only", "-z", "--diff-filter=ACMR", base, "--") + git(repo, "ls-files", "-z", "--others", "--exclude-standard")).split("\0")
        findings = check_files(repo, profile, staged, base, None, staged=True)
        findings.extend(check_files(repo, profile, [path for path in names if path and (repo / path).is_file()], base, None))
        return list(dict.fromkeys(findings))
    except (DiffError, OSError) as error:
        return [range_finding(COMMENTS, DiffError(str(error)))]
