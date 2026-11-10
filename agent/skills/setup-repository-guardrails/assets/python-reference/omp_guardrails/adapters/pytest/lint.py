"""Source-pattern rules for pytest suites, run with ``ast-grep-py`` (adapter item 4).

Rules live in ``rules/*.yml``. Each file is one JSON object, which is valid YAML flow syntax, so
the ``ast-grep`` CLI and editors read the files as YAML while this module reads them with the
standard ``json`` module and needs no YAML parser. Keys beyond ast-grep's own:

- ``scope``: ``tests`` (test files) or ``production`` (files of the owned packages).
- ``target``: optional. When set, a match is a finding only if the named expression resolves
  inside an owned package (TEST.no-self-mocking): ``first-argument``,
  ``keyword:<name>,<name>`` or ``assigned-attribute-object``.
- ``message`` and ``fix``: finding text; ``{target}`` and ``{match}`` are filled in.

Owned packages (D2) are the top-level packages that own the profile's ``public_entry_points``
keys. Test files are files under a tier ``location``, files matching ``layout.test_file_patterns``
and ``conftest.py`` files. Production files are the other Python files with an owned package
in their path. A finding is waived where ``house_style.exceptions`` holds an entry whose ``rule``
is the finding's rule and whose ``path`` matches the file.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from importlib import resources
from pathlib import Path, PurePosixPath

from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, matches_any, name_matches

RULES_PACKAGE = "omp_guardrails.adapters.pytest"
SKIPPED_PARTS = frozenset({".git", ".venv", "venv", "node_modules", "build", "dist", "__pycache__"})


@dataclass(frozen=True)
class Rule:
    name: str
    rule_id: str
    scope: str
    target: str | None
    message: str
    fix: str
    config: dict


def load_rules() -> list[Rule]:
    folder = resources.files(RULES_PACKAGE).joinpath("rules")
    rules = []
    for entry in sorted(folder.iterdir(), key=lambda item: item.name):
        if not entry.name.endswith(".yml"):
            continue
        data = json.loads(entry.read_text(encoding="utf-8"))
        config = {"rule": data["rule"]}
        for key in ("constraints", "utils"):
            if key in data:
                config[key] = data[key]
        rules.append(
            Rule(
                entry.name,
                data["id"],
                data["scope"],
                data.get("target"),
                data["message"],
                data["fix"],
                config,
            )
        )
    return rules


def owned_packages(profile: dict) -> frozenset[str]:
    """D2: the top-level packages owning ``public_entry_points`` (``src/`` prefixes dropped)."""
    entries = profile.get("public_entry_points")
    names = set()
    for key in entries if isinstance(entries, dict) else {}:
        parts = [part for part in str(key).replace("/", ".").split(".") if part]
        if parts and parts[0] == "src":
            parts = parts[1:]
        if parts:
            names.add(parts[0])
    return frozenset(names)


def test_patterns(profile: dict) -> tuple[list[str], list[str]]:
    tiers = profile.get("tiers")
    locations = [
        location
        for spec in (tiers.values() if isinstance(tiers, dict) else [])
        if isinstance(spec, dict)
        for location in as_list(spec.get("location"))
    ]
    layout = profile.get("layout")
    patterns = as_list(layout.get("test_file_patterns")) if isinstance(layout, dict) else []
    return locations, patterns


def is_test_file(path: str, locations: list[str], patterns: list[str]) -> bool:
    if PurePosixPath(path).name == "conftest.py" or matches_any(path, locations):
        return True
    return any(name_matches(path, pattern) for pattern in patterns)


def python_files(repo: Path, argv: list[str]) -> list[str]:
    if argv:
        return sorted({path for path in argv if path.endswith(".py")})
    from omp_guardrails.structure.inventory import InventoryError, repo_paths

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
    """Local name -> dotted origin for absolute imports in one file."""
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


def resolve(node: object, imports: dict[str, str]) -> str | None:
    """Dotted origin of a string target or an imported expression, else None."""
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


def arguments(call: object) -> list[object]:
    node = call.field("arguments")
    if node is None:
        return []
    return [child for child in node.children() if child.is_named() and child.kind() != "comment"]


def target_nodes(match: object, target: str) -> list[object]:
    if target == "assigned-attribute-object":
        left = match.field("left")
        value = left.field("object") if left is not None else None
        return [value] if value is not None else []
    positional = [item for item in arguments(match) if item.kind() != "keyword_argument"]
    if target == "first-argument":
        return positional[:1]
    if target.startswith("keyword:"):
        wanted = set(target.split(":", 1)[1].split(","))
        found = []
        for item in arguments(match):
            name = item.field("name")
            if item.kind() == "keyword_argument" and name is not None and name.text() in wanted:
                value = item.field("value")
                if value is not None:
                    found.append(value)
        return found
    raise ValueError(f"unknown rule target {target!r}")


def owned_target(match: object, target: str, imports: dict[str, str], owned: frozenset[str]):
    for node in target_nodes(match, target):
        origin = resolve(node, imports)
        if origin and origin.split(".")[0] in owned:
            return origin
    return None


def waived(rule_id: str, path: str, profile: dict) -> bool:
    house_style = profile.get("house_style")
    exceptions = house_style.get("exceptions") if isinstance(house_style, dict) else None
    for entry in exceptions if isinstance(exceptions, list) else []:
        if not isinstance(entry, dict) or entry.get("rule") != rule_id:
            continue
        pattern = str(entry.get("path", ""))
        if path == pattern or fnmatchcase(path, pattern):
            return True
    return False


def file_findings(path: str, source: str, rules: list[Rule], owned: frozenset[str]):
    from ast_grep_py import SgRoot

    root = SgRoot(source, "python").root()
    imports = import_map(source)
    findings = []
    for rule in rules:
        for match in root.find_all(rule.config):
            target = None
            if rule.target:
                target = owned_target(match, rule.target, imports, owned)
                if target is None:
                    continue
            line = match.range().start.line + 1
            snippet = " ".join(match.text().split())[:80]
            message = rule.message.format(target=target, match=snippet)
            findings.append(Finding(rule.rule_id, path, f"line {line}: {message}", rule.fix))
    return findings


def lint(repo: Path, profile: dict, paths: list[str]) -> list[Finding]:
    owned = owned_packages(profile)
    locations, patterns = test_patterns(profile)
    rules = load_rules()
    findings: list[Finding] = []
    for path in python_files(repo, paths):
        if is_test_file(path, locations, patterns):
            scoped = [rule for rule in rules if rule.scope == "tests"]
        elif owned & {*PurePosixPath(path).parts[:-1], PurePosixPath(path).stem}:
            scoped = [rule for rule in rules if rule.scope == "production"]
        else:
            continue
        try:
            source = (repo / path).read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        findings.extend(
            finding
            for finding in file_findings(path, source, scoped, owned)
            if not waived(finding.rule_id, path, profile)
        )
    return findings
