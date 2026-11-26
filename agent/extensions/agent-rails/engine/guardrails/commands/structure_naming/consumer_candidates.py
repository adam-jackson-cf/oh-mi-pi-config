from __future__ import annotations

import argparse
import ast
import json
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming.scan_scope import (
    is_test_path,
    is_vendored_path,
)
from guardrails.context.changes import (
    DiffError,
    changed_paths,
    read_blobs,
    tree_entries,
)
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.reader import checker_settings
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any
from guardrails.subcommand.dispatch import SUBCOMMANDS

RULE = "STRUCT.consumer-candidates"
EXCEPTION_RULE = "structure-naming/consumer_placement"
ROOT_NON_PACKAGE = frozenset({"build", "fixtures", "migrations", "scripts", "tests"})
UI_LIBRARIES = frozenset({
    "altair",
    "blessed",
    "bokeh",
    "curses",
    "dash",
    "flet",
    "gradio",
    "jinja2",
    "matplotlib",
    "nicegui",
    "panel",
    "plotly",
    "prettytable",
    "prompt_toolkit",
    "pygal",
    "questionary",
    "rich",
    "seaborn",
    "streamlit",
    "tabulate",
    "textual",
    "tkinter",
    "urwid",
})
MARKERS = (
    ("main_guard", re.compile(r"""^\s*if\s+__name__\s*==\s*['"]__main__['"]""", re.M)),
    ("argparse", re.compile(r"\bArgumentParser\s*\(")),
    ("click", re.compile(r"@click\.(command|group)\b")),
    ("typer", re.compile(r"\btyper\.(Typer|run)\s*\(")),
    ("fire", re.compile(r"\bfire\.Fire\s*\(")),
    ("streamlit", re.compile(r"^\s*(import\s+streamlit|from\s+streamlit\b)", re.M)),
    ("dash", re.compile(r"\b(dash\.)?Dash\s*\(\s*__name__")),
    ("gradio", re.compile(r"^\s*(import\s+gradio|from\s+gradio\b)", re.M)),
    ("flask", re.compile(r"\bFlask\s*\(\s*__name__")),
    ("fastapi", re.compile(r"\bFastAPI\s*\(")),
    (
        "textual_app",
        re.compile(r"^\s*class\s+\w+\(\s*(textual\.app\.)?App(\[[^\]]*\])?\s*\)", re.M),
    ),
    ("rich_live", re.compile(r"from\s+rich\.live\s+import|rich\.live\.Live\b")),
)
UI_APP_MARKERS = frozenset({"dash", "gradio", "rich_live", "streamlit", "textual_app"})
MANIFESTS = frozenset({"pyproject.toml", "setup.cfg", "setup.py"})
MAX_SOURCE_BYTES = 1_500_000
STATE_LINES = 60
REGEX_IMPORT = re.compile(
    r"^\s*(from\s+(\.*)([\w.]*)\s+import\s+([\w, ()*]+)|import\s+([\w., ]+))", re.M
)
Import = tuple[str, list[str], int, int, str]


def parent(path: str) -> str:
    folder = PurePosixPath(path).parent.as_posix()
    return "" if folder == "." else folder


def join(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def excluded(path: str, tree: dict[str, str]) -> bool:
    if is_vendored_path(path):
        return True
    folder = parent(path)
    while True:
        if join(folder, "SKILL.md") in tree:
            return True
        if not folder:
            return False
        folder = parent(folder)


def has_init(tree: dict[str, str], folder: str) -> bool:
    return bool(folder) and join(folder, "__init__.py") in tree


def package_dirs(tree: dict[str, str], path: str) -> tuple[str | None, str | None]:
    folder, nearest = parent(path), None
    while folder:
        if has_init(tree, folder):
            nearest = folder
            break
        folder = parent(folder)
    if not nearest:
        return None, None
    top = nearest
    while has_init(tree, parent(top)):
        top = parent(top)
    return nearest, top


def module_names(tree: dict[str, str], path: str) -> set[str]:
    stem = path[:-3]
    if stem == "__init__" or stem.endswith("/__init__"):
        stem = stem[:-9]
    names = {stem.replace("/", ".")}
    if has_init(tree, parent(path)):
        top = parent(path)
        while has_init(tree, parent(top)):
            top = parent(top)
        base = parent(top)
        names.add((stem[len(base) + 1 :] if base else stem).replace("/", "."))
    return {name for name in names if name}


def regex_imports(source: str) -> list[Import]:
    found: list[Import] = []
    for match in REGEX_IMPORT.finditer(source):
        line = source[: match.start()].count("\n") + 1
        if match.group(5):
            for name in match.group(5).split(","):
                bare = name.strip().split(" as ")[0].strip()
                if bare:
                    found.append((bare, [], 0, line, "import " + bare))
            continue
        names = [
            item.strip().split(" as ")[0]
            for item in match.group(4).strip("() ").split(",")
            if item.strip()
        ]
        found.append((
            match.group(3),
            names,
            len(match.group(2)),
            line,
            match.group(0).strip(),
        ))
    return found


def dynamic_import(node: ast.Call) -> Import | None:
    function = node.func
    name = (
        function.attr
        if isinstance(function, ast.Attribute)
        else function.id
        if isinstance(function, ast.Name)
        else ""
    )
    if name not in {"import_module", "__import__"} or not node.args:
        return None
    target = node.args[0]
    if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
        return None
    return (target.value.lstrip("."), [], 0, node.lineno, f"{name}({target.value!r})")


def parse_imports(source: str) -> list[Import]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return regex_imports(source)
    found: list[Import] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                (alias.name, [], 0, node.lineno, "import " + alias.name)
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names]
            text = (
                f"from {'.' * node.level}{node.module or ''} import {', '.join(names)}"
            )
            found.append((node.module or "", names, node.level, node.lineno, text))
        elif isinstance(node, ast.Call) and (dynamic := dynamic_import(node)):
            found.append(dynamic)
    return sorted(found, key=lambda item: item[3])


def resolve(importer: str, module: str, names: list[str], level: int) -> set[str]:
    if level:
        base = parent(importer)
        for _ in range(level - 1):
            base = parent(base)
        module = ".".join(part for part in (base.replace("/", "."), module) if part)
    found = {module} if module else set()
    found.update(
        f"{module}.{name}" if module else name for name in names if name != "*"
    )
    return found


class ImportIndex:
    def __init__(self, tree: dict[str, str]) -> None:
        self.tree = tree
        python = [path for path in tree if path.endswith(".py")]
        self.by_name: dict[str, set[str]] = {}
        for path in python:
            for name in module_names(tree, path):
                self.by_name.setdefault(name, set()).add(path)
        self.roots = {
            parent(top) for path in python if (top := package_dirs(tree, path)[1])
        }
        self.raw: dict[str, list[Import]] = {}
        self.imported_by: dict[str, set[str]] = {}
        for path in python:
            self.raw[path] = parse_imports(tree[path])
            for target in self.targets(path) - {path}:
                self.imported_by.setdefault(target, set()).add(path)

    def targets(self, path: str) -> set[str]:
        hits: set[str] = set()
        folder = parent(path)
        for module, names, level, _, _ in self.raw[path]:
            for name in resolve(path, module, names, level):
                hits |= self.by_name.get(name, set())
                if not level:
                    hits |= self.file_hits(name.replace(".", "/"), folder)
        return hits

    def file_hits(self, relative: str, folder: str) -> set[str]:
        hits = set()
        for root in {folder, "", "src"} | self.roots:
            full = join(root, relative)
            for candidate in (full + ".py", full + "/__init__.py"):
                if candidate in self.tree:
                    hits.add(candidate)
        return hits


DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")


def docstring_nodes(tree: ast.Module) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
            ):
                found.add(id(body[0].value))
    return found


def string_literals(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    docs = docstring_nodes(tree)
    return [
        node.value.strip()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docs
        and (node.value.strip().endswith(".py") or DOTTED.match(node.value.strip()))
    ]


class PathReferences:
    def __init__(self, tree: dict[str, str]) -> None:
        self.tree = tree
        self.literals = {
            path: found
            for path in tree
            if path.endswith(".py")
            and not is_test_path(path)
            and (found := string_literals(tree[path]))
        }

    def referenced(self, path: str) -> bool:
        name = PurePosixPath(path).name
        top = package_dirs(self.tree, path)[1] or parent(path)
        dotted = module_names(self.tree, path)
        for source, literals in self.literals.items():
            if source == path:
                continue
            for literal in literals:
                if literal in dotted:
                    return True
                text = literal.removeprefix("./")
                if "/" in text and (path == text or path.endswith("/" + text)):
                    return True
                if text == name and (source == top or source.startswith(top + "/")):
                    return True
        return False


def manifest_targets(path: str, text: str) -> list[tuple[str, str]]:
    name = PurePosixPath(path).name
    if name in {"setup.py", "setup.cfg"}:
        return [
            (match.group(1), f"{match.group(2)}:{match.group(3)}")
            for match in re.finditer(
                r"""['"]?([\w.-]+)\s*=\s*([\w.]+)\s*:\s*([\w.]+)""", text
            )
        ]
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    project = data.get("project", {})
    poetry = data.get("tool", {}).get("poetry", {})
    found = []
    for section in (
        project.get("scripts", {}),
        project.get("gui-scripts", {}),
        poetry.get("scripts", {}),
    ):
        for key, value in (section or {}).items():
            target = (
                value.get("reference", value.get("callable", ""))
                if isinstance(value, dict)
                else value
            )
            if isinstance(target, str):
                found.append((key, target))
    return found


def project_scripts(tree: dict[str, str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path, text in tree.items():
        if PurePosixPath(path).name not in MANIFESTS:
            continue
        for key, target in manifest_targets(path, text):
            module = target.split(":")[0].strip()
            found.setdefault(module, []).append(f'{path} [scripts] {key} = "{target}"')
    return found


def run_markers(source: str) -> list[str]:
    return [name for name, pattern in MARKERS if pattern.search(source)]


def features(
    index: ImportIndex, path: str, scripts: dict[str, list[str]]
) -> dict[str, Any]:
    tree = index.tree
    nearest, top = package_dirs(tree, path)
    top_name = PurePosixPath(top).name if top else None
    imports = index.raw.get(path) or parse_imports(tree[path])
    own = [
        text
        for module, _, level, _, text in imports
        if level or (top_name and (module.split(".")[0] if module else "") == top_name)
    ]
    ui = sorted({
        module.split(".")[0]
        for module, _, level, _, _ in imports
        if not level and module and module.split(".")[0] in UI_LIBRARIES
    })
    markers = run_markers(tree[path])
    name = PurePosixPath(path).name
    return {
        "package_dir": nearest,
        "package_name": top_name,
        "package_init": join(top, "__init__.py") if top else None,
        "run_markers": markers,
        "imported_by": len(index.imported_by.get(path, ())),
        "own_package_imports": len(own),
        "ui_libs": ui,
        "ui_app_marker": any(marker in UI_APP_MARKERS for marker in markers),
        "in_project_scripts": any(
            scripts.get(module) for module in module_names(tree, path)
        ),
        "dunder_main": name == "__main__.py",
        "test_path": is_test_path(path),
        "imports": [item[4] for item in imports],
    }


def proposer_score(found: dict[str, Any]) -> float:
    score = 0.5
    score += 0.20 if found["ui_libs"] else 0.0
    score += 0.10 if found["ui_app_marker"] else 0.0
    score += 0.15 * min(int(found["own_package_imports"]), 3) / 3
    score -= 0.10 if found["own_package_imports"] == 0 else 0.0
    score -= 0.35 if found["in_project_scripts"] else 0.0
    score -= 0.20 if found["dunder_main"] else 0.0
    score -= 0.25 if found["test_path"] else 0.0
    score -= 0.50 if found["imported_by"] else 0.0
    return round(min(0.98, max(0.02, score)), 4)


def candidate_path(tree: dict[str, str], path: str) -> bool:
    if not path.endswith(".py") or PurePosixPath(path).name == "__init__.py":
        return False
    if excluded(path, tree) or path.split("/")[0] in ROOT_NON_PACKAGE:
        return False
    return bool(package_dirs(tree, path)[0])


def docstring_line(source: str) -> str:
    try:
        doc = ast.get_docstring(ast.parse(source))
    except (SyntaxError, ValueError):
        doc = None
    return (doc or "").strip().split("\n")[0].strip()


def consumer_state(
    tree: dict[str, str], path: str, found: dict[str, Any]
) -> dict[str, Any]:
    package: dict[str, str] = {"name": found["package_name"] or "(none)"}
    init = found["package_init"]
    description = docstring_line(tree.get(init, "")) if init else ""
    if description:
        package["description"] = description
    return {
        "file": {
            "path": path,
            "first_60_lines": "\n".join(tree[path].split("\n")[:STATE_LINES]),
            "imports": found["imports"][:STATE_LINES],
        },
        "package": package,
    }


def unused(facts: dict[str, Any]) -> bool:
    return bool(
        facts["run_markers"]
        and facts["imported_by"] == 0
        and not facts["in_project_scripts"]
        and not facts["referenced_by_path"]
    )


def propose(tree: dict[str, str]) -> list[tuple[str, dict[str, Any]]]:
    index = ImportIndex(tree)
    scripts = project_scripts(tree)
    references = PathReferences(tree)
    found = []
    for path in sorted(tree):
        if not candidate_path(tree, path):
            continue
        facts = features(index, path, scripts)
        facts["referenced_by_path"] = references.referenced(path)
        if unused(facts):
            facts["proposer_score"] = proposer_score(facts)
            found.append((path, facts))
    return found


def wanted(path: str, kind: str, size: str) -> bool:
    name = PurePosixPath(path).name
    return (
        kind == "blob"
        and (path.endswith(".py") or name in MANIFESTS)
        and size.isdigit()
        and int(size) < MAX_SOURCE_BYTES
    )


def head_tree(repo: Path, head: str, excluded_paths: list[str]) -> dict[str, str]:
    tree: dict[str, str] = {}
    want: list[tuple[str, str]] = []
    for path, (_, kind, oid, size) in tree_entries(repo, head).items():
        if matches_any(path, excluded_paths):
            continue
        if kind == "blob" and PurePosixPath(path).name == "SKILL.md":
            tree[path] = ""
        elif wanted(path, kind, size):
            want.append((path, oid))
    blobs = read_blobs(repo, sorted({oid for _, oid in want}))
    for path, oid in want:
        tree[path] = blobs[oid].decode("utf-8", "replace")
    return tree


def exempt(path: str, config: dict[str, Any]) -> bool:
    return not apply_exceptions([Finding(EXCEPTION_RULE, path, "", "")], config)


def consumer_candidates(
    tree: dict[str, str], changed: set[str], config: dict[str, Any]
) -> list[dict[str, Any]]:
    return [
        {
            "path": path,
            "proposer_score": facts["proposer_score"],
            "run_markers": facts["run_markers"],
            "state": consumer_state(tree, path, facts),
        }
        for path, facts in propose(tree)
        if path in changed and not exempt(path, config)
    ]


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    parser = argparse.ArgumentParser(
        description=SUBCOMMANDS["consumer-candidates"].summary
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    excluded_paths = as_list(
        checker_settings(config, "check").get("exclude")
    ) + as_list(checker_settings(config, "python").get("excluded_paths"))
    try:
        tree = head_tree(repo, args.head, excluded_paths)
        found = consumer_candidates(
            tree, changed_paths(repo, args.base, args.head, "AMR"), config
        )
    except (DiffError, ValueError) as error:
        return [
            Finding(
                RULE,
                ".",
                str(error),
                "provide readable base and head commits in this repository",
            )
        ]
    print(json.dumps({"candidates": found}, sort_keys=True))
    return []
