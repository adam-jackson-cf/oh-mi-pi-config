"""AST source checks for Vitest and fast-check, with shared repository inventory."""
from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from importlib import resources
from pathlib import Path, PurePosixPath

from omp_guardrails.adapters.pytest.lint import test_patterns, waived
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import matches_any, name_matches, repo_paths, normalized_path

from .cases import CollectionError, arguments, children, imports, parse, qualified, string, walk

SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")
SKIPPED = frozenset({".git", "node_modules", "dist", "build", ".venv", "coverage"})


@dataclass(frozen=True)
class Rule:
    rule_id: str
    scope: str
    target: str
    message: str
    fix: str
    config: dict


def load_rules() -> list[Rule]:
    text = resources.files("omp_guardrails.adapters.vitest").joinpath("patterns.yml").read_text(encoding="utf-8")
    return [Rule(item["id"], item["scope"], item["target"], item["message"], item["fix"], {"rule": item["rule"]}) for item in json.loads(text)]


def source_files(repo: Path, paths: list[str]) -> list[str]:
    return sorted({path for raw in (paths or repo_paths(repo))
                   if (path := normalized_path(repo, raw)) is not None
                   and path.endswith(SUFFIXES) and not SKIPPED.intersection(PurePosixPath(path).parts)})


def is_test(path: str, profile: dict) -> bool:
    locations, patterns = test_patterns(profile)
    return matches_any(path, locations) or any(name_matches(path, item) for item in patterns)


def owned_roots(profile: dict, repo: Path | None = None) -> tuple[str, ...]:
    entries = profile.get("public_entry_points", {})
    roots = set()
    for raw in entries if isinstance(entries, dict) else ():
        path = str(raw).replace("\\", "/")
        if path.endswith(SUFFIXES):
            roots.add(posixpath.dirname(path) or ".")
        else:
            roots.add(path.rstrip("/"))
    if repo is not None:
        package_names = set(roots)
        for raw in repo_paths(repo):
            manifest = normalized_path(repo, raw)
            if manifest is None or PurePosixPath(manifest).name != "package.json" or SKIPPED.intersection(PurePosixPath(manifest).parts):
                continue
            data = json.loads((repo / manifest).read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("name") in package_names:
                roots.add(posixpath.dirname(manifest) or ".")
    return tuple(sorted(roots))


def owned_path(path: str, roots: tuple[str, ...]) -> bool:
    return any(root == "." or path == root or path.startswith(root + "/") for root in roots)


def module_path(path: str, module: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(path), module)) if module.startswith(".") else module


def owned_origin(path: str, origin: str, roots: tuple[str, ...]) -> bool:
    module = origin.split("#", 1)[0]
    if module.startswith("."):
        resolved = module_path(path, module)
        return not resolved.startswith("../") and owned_path(resolved, roots)
    return any(
        module == root or module.startswith(root + "/")
        or (root.startswith("src/") and (module == root[4:] or module.startswith(root[4:] + "/")))
        for root in roots if root != "."
    )


def api(node, aliases):
    return qualified(node, aliases).replace("vitest#", "vitest.").removeprefix("vitest.")


def object_pairs(node):
    if node is None or node.kind() != "object":
        return {}
    return {(string(item.field("key")) or item.field("key").text()): item.field("value")
            for item in children(node) if item.kind() == "pair" and item.field("key") is not None}


def integer_seed(node) -> bool:
    if node is None:
        return False
    try:
        value = json.loads(node.text())
    except ValueError:
        return False
    return type(value) is int


def mock_origin(node, aliases):
    value = string(node)
    if value is not None:
        return value
    function = node.field("function") if node.kind() == "call_expression" else None
    if function is not None and (function.kind() == "import" or api(function, aliases) == "import"):
        args = arguments(node)
        return string(args[0]) if args else None
    if node.kind() == "new_expression":
        constructor = node.field("constructor")
        return mock_origin(constructor, aliases) if constructor is not None else None
    if function is not None:
        return mock_origin(function, aliases)
    head = node
    while head is not None and head.kind() in {"member_expression", "subscript_expression"}:
        head = head.field("object")
    if head is not None and head.kind() == "identifier" and head.text() in aliases:
        return qualified(node, aliases) or None
    return None


def violation(rule: Rule, node, path: str, aliases, roots, seeded: bool):
    if rule.target == "environment":
        return node.text()
    if rule.target == "hook":
        parent = node.parent()
        # Declaration names only; ordinary mentions and string values are not hooks.
        if parent is not None and parent.kind() in {"variable_declarator", "function_declaration", "class_declaration", "method_definition", "required_parameter", "optional_parameter"}:
            name = parent.field("name") or parent.field("pattern")
            if name is not None and name.range().start.index == node.range().start.index:
                return node.text()
        return None
    if rule.target == "mock-assignment":
        left = node.field("left")
        origin = mock_origin(left, aliases) if left is not None else None
        if origin and owned_origin(path, origin, roots):
            return origin
        return None
    function = node.field("function")
    name = api(function, aliases)
    args = arguments(node)
    if rule.target == "mock-call":
        if name not in {"vi.mock", "vi.doMock", "vi.spyOn", "vi.mockObject", "vi.mocked"} or not args:
            return None
        origin = mock_origin(args[0], aliases)
        return origin if origin and owned_origin(path, origin, roots) else None
    if rule.target == "evasion":
        if name.split(".")[0] not in {"test", "it", "describe", "suite"}:
            return None
        if set(name.split(".")) & {"skip", "skipIf", "runIf", "only", "fails", "todo", "retry"}:
            return name
        for arg in args:
            retry = object_pairs(arg).get("retry")
            if retry is not None and retry.text() != "0":
                return "retry=" + retry.text()
        return None
    if rule.target == "property":
        origin = qualified(function, aliases)
        if origin not in {"fast-check.assert", "fast-check.check", "fast-check#assert", "fast-check#check"}:
            return None
        options = object_pairs(args[1]) if len(args) > 1 else {}
        explicit = options.get("seed")
        return None if integer_seed(explicit) or (explicit is None and seeded) else origin
    raise ValueError(f"unknown Vitest pattern target {rule.target}")


def file_findings(path: str, source: str, rules: list[Rule], owned: tuple[str, ...]) -> list[Finding]:
    root = parse(path, source)
    aliases = imports(root)
    seeded = False
    # Global configuration must precede the property invocation, not merely occur in the file.
    configured = []
    for node in root.find_all({"rule": {"kind": "call_expression"}}):
        if qualified(node.field("function"), aliases) in {"fast-check.configureGlobal", "fast-check#configureGlobal"}:
            args = arguments(node)
            parent = node.parent()
            top_level = parent is not None and parent.kind() == "expression_statement" and parent.parent() is not None and parent.parent().kind() == "program"
            if top_level and args and integer_seed(object_pairs(args[0]).get("seed")):
                configured.append(node.range().end.index)
    findings = []
    for rule in rules:
        for node in root.find_all(rule.config):
            seeded = any(end < node.range().start.index for end in configured)
            target = violation(rule, node, path, aliases, owned, seeded)
            if target is not None:
                findings.append(Finding(rule.rule_id, path, f"line {node.range().start.line + 1}: " + rule.message.format(target=target), rule.fix))
    return findings


def test_only_findings(sources: dict[str, str], profile: dict, reported: set[str], roots: tuple[str, ...] | None = None) -> list[Finding]:
    """Resolve imported references to declarations; same-spelled unrelated symbols do not count."""
    roots = owned_roots(profile) if roots is None else roots
    trees = {path: parse(path, text) for path, text in sources.items()}
    definitions = {}
    for path, root in trees.items():
        if is_test(path, profile) or not owned_path(path, roots):
            continue
        for statement in children(root):
            for node in children(statement) if statement.kind() == "export_statement" else [statement]:
                candidates = children(node) if node.kind() in {"lexical_declaration", "variable_declaration"} else [node]
                for candidate in candidates:
                    if candidate.kind() in {"function_declaration", "class_declaration", "variable_declarator"}:
                        name = candidate.field("name")
                        if name is not None and name.kind() in {"identifier", "type_identifier"}:
                            definitions[(path, name.text())] = (candidate, name)
    uses = {key: set() for key in definitions}
    for path, root in trees.items():
        aliases = imports(root)
        test = is_test(path, profile)
        # Import declarations alone are not usage.
        for node in walk(root):
            if node.kind() not in {"identifier", "shorthand_property_identifier", "member_expression"}:
                continue
            parent = node.parent()
            ancestor = parent
            in_import = False
            while ancestor is not None:
                if ancestor.kind() == "import_statement":
                    in_import = True
                    break
                ancestor = ancestor.parent()
            if in_import:
                continue
            origin = qualified(node, aliases)
            local_key = (path, node.text())
            own_definition = definitions.get(local_key)
            if own_definition is not None:
                declaration, _ = own_definition
                # Recursive implementation references do not establish production use.
                within = declaration.range().start.index <= node.range().start.index <= declaration.range().end.index
                if not within:
                    uses[local_key].add(test)
            if "#" in origin:
                module, symbol = origin.split("#", 1)
            elif "." in origin:
                module, _, symbol = origin.rpartition(".")
            else:
                continue
            resolved = module_path(path, module)
            candidates = {resolved, *(resolved + suffix for suffix in SUFFIXES), *(resolved + "/index" + suffix for suffix in SUFFIXES)}
            for owner in candidates:
                key = (owner, symbol)
                if key in definitions:
                    uses[key].add(test)
    entries = profile.get("public_entry_points", {})
    public = set()
    for path, names in entries.items() if isinstance(entries, dict) else ():
        for name in names if isinstance(names, list) else []:
            if isinstance(name, str):
                public.add((path, name))
    return [Finding("TEST.no-test-hooks", path, f"line {declared.range().start.line + 1}: production symbol {name!r} is used only by tests", "remove the test-only symbol or exercise the real production API")
            for (path, name), (_, declared) in definitions.items()
            if path in reported and uses[(path, name)] == {True} and (path, name) not in public]


def lint(repo: Path, profile: dict, paths: list[str]) -> list[Finding]:
    all_paths = source_files(repo, [])
    selected = set(source_files(repo, paths)) if paths else set(all_paths)
    roots = owned_roots(profile, repo)
    sources = {}
    findings = []
    for path in all_paths:
        if not is_test(path, profile) and not owned_path(path, roots):
            continue
        try:
            source = (repo / path).read_text(encoding="utf-8")
            parse(path, source)
            sources[path] = source
        except (OSError, UnicodeError) as error:
            rule = "TEST.no-evasion" if is_test(path, profile) else "TEST.no-test-hooks"
            findings.append(Finding(rule, path, f"cannot read source for AST analysis: {error}", "restore readable UTF-8 TypeScript/JavaScript source before running adapter lint"))
        except CollectionError as error:
            rule = "TEST.no-evasion" if is_test(path, profile) else "TEST.no-test-hooks"
            findings.append(Finding(rule, path, f"cannot parse source for AST analysis: {error}", "repair JavaScript/TypeScript syntax before running adapter lint"))
    rules = load_rules()
    production = set()
    for path in sorted(selected):
        if path not in sources:
            continue
        if is_test(path, profile):
            scope = "tests"
        elif owned_path(path, roots):
            scope = "production"
            production.add(path)
        else:
            continue
        findings.extend(file_findings(path, sources[path], [rule for rule in rules if rule.scope == scope], roots))
    findings.extend(test_only_findings(sources, profile, production, roots))
    return [finding for finding in findings if not waived(finding.rule_id, finding.path, profile)]
