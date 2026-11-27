from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.commands.structure_naming.scan_scope import (
    is_vendored_path,
    proposed_source,
)
from guardrails.context.changes import (
    DiffError,
    changed_paths,
    read_blobs,
    tree_entries,
)
from guardrails.context.config.house_style import apply_exceptions
from guardrails.context.config.reader import checker_settings
from guardrails.context.config.suite_files import is_test_source
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import as_list, matches_any
from guardrails.foundation.source import modules, python, tree
from guardrails.foundation.source.tree import SourceError

if TYPE_CHECKING:
    from guardrails.foundation.source.tree import SgNode
from guardrails.subcommand.dispatch import SUBCOMMANDS

RULE = "STRUCT.consumer-candidates"
EXCEPTION_RULE = "structure-naming/consumer_placement"
ROOT_NON_PACKAGE = frozenset({"fixtures", "migrations", "scripts", "tests"})
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
READS = frozenset({"python"})
MAX_SOURCE_BYTES = 1_500_000
STATE_LINES = 60
STRING_KINDS = frozenset({"string", "concatenated_string"})
Import = tuple[python.PythonImport, str]


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
    return bool(folder) and join(folder, modules.package_marker()) in tree


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


def import_text(item: python.PythonImport) -> str:
    if item.statement == "import":
        return f"import {item.module}"
    return f"from {'.' * item.level}{item.module} import {', '.join(item.names)}"


def parse_imports(root: SgNode) -> list[Import]:
    found = [(item, import_text(item)) for item in python.imports(root)]
    found.extend(
        (item, f"{callee.rpartition('.')[2]}({'.' * item.level + item.module!r})")
        for callee, item in python.dynamic_imports(root)
    )
    return sorted(found, key=lambda entry: entry[0].line)


def string_literals(root: SgNode) -> list[str]:
    """The path-like and dotted string literals of a file, docstrings excepted."""
    docs = {
        tree.named(statement)[0].range().start.index
        for statement in python.docstrings(root)
    }
    found = []
    for node in tree.of_kinds(root, STRING_KINDS):
        parent_node = node.parent()
        if (
            (parent_node is not None and parent_node.kind() in STRING_KINDS)
            or node.range().start.index in docs
            or not python.text_literal(node)
        ):
            continue
        value = str(python.literal_value(node.text())).strip()
        if proposed_source(value, READS) or DOTTED.match(value):
            found.append(value)
    return found


@dataclass(frozen=True)
class Reading:
    imports: list[Import]
    literals: list[str]
    docstring: str


def reading(path: str, source: str) -> Reading | None:
    """The syntax a proposer reads from a Python file. An unparseable file (tree-sitter
    rejects it, or CPython rejects one of its string literals or its docstring) is
    skipped: it is no candidate, imports nothing and names no path."""
    try:
        root = tree.parse(path, source)
        return Reading(
            parse_imports(root), string_literals(root), python.body_docstring(root)
        )
    except tree.SourceError:
        return None


def readings(files: dict[str, str]) -> dict[str, Reading]:
    return {
        path: found
        for path, text in files.items()
        if proposed_source(path, READS) and (found := reading(path, text)) is not None
    }


class ImportIndex:
    def __init__(self, tree: dict[str, str], read: dict[str, Reading]) -> None:
        self.tree = tree
        self.index = modules.PythonIndex(
            path for path in tree if proposed_source(path, READS)
        )
        self.raw = {path: found.imports for path, found in read.items()}
        self.imported_by: dict[str, set[str]] = {}
        for path, found in self.raw.items():
            for item, _ in found:
                for target in self.index.resolve(path, item):
                    self.imported_by.setdefault(target, set()).add(path)

    def module_names(self, path: str) -> list[str]:
        return self.index.names.get(path, [])


DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")


class PathReferences:
    def __init__(
        self, index: ImportIndex, read: dict[str, Reading], config: dict[str, Any]
    ) -> None:
        self.index = index
        self.literals = {
            path: found.literals
            for path, found in read.items()
            if not is_test_source(path, config) and found.literals
        }

    def referenced(self, path: str) -> bool:
        tree = self.index.tree
        name = PurePosixPath(path).name
        top = package_dirs(tree, path)[1] or parent(path)
        dotted = set(self.index.module_names(path))
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
    return modules.project_scripts(path, text)


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
    index: ImportIndex,
    path: str,
    scripts: dict[str, list[str]],
    config: dict[str, Any],
) -> dict[str, Any]:
    tree = index.tree
    nearest, top = package_dirs(tree, path)
    top_name = PurePosixPath(top).name if top else None
    imports = index.raw[path]
    own = [
        text
        for item, text in imports
        if item.level or (top_name and item.module.split(".")[0] == top_name)
    ]
    ui = sorted({
        item.module.split(".")[0]
        for item, _ in imports
        if not item.level and item.module.split(".")[0] in UI_LIBRARIES
    })
    markers = run_markers(tree[path])
    name = PurePosixPath(path).name
    return {
        "package_dir": nearest,
        "package_name": top_name,
        "package_init": join(top, modules.package_marker()) if top else None,
        "run_markers": markers,
        "imported_by": len(index.imported_by.get(path, ())),
        "own_package_imports": len(own),
        "ui_libs": ui,
        "ui_app_marker": any(marker in UI_APP_MARKERS for marker in markers),
        "in_project_scripts": any(
            scripts.get(module) for module in index.module_names(path)
        ),
        "dunder_main": name == "__main__.py",
        "test_path": is_test_source(path, config),
        "imports": [text for _, text in imports],
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
    if (
        not proposed_source(path, READS)
        or PurePosixPath(path).name == modules.package_marker()
    ):
        return False
    if excluded(path, tree) or path.split("/")[0] in ROOT_NON_PACKAGE:
        return False
    return bool(package_dirs(tree, path)[0])


def docstring_line(found: Reading | None) -> str:
    doc = "" if found is None else found.docstring
    return doc.strip().split("\n")[0].strip()


def consumer_state(
    tree: dict[str, str], path: str, found: dict[str, Any]
) -> dict[str, Any]:
    package: dict[str, str] = {"name": found["package_name"] or "(none)"}
    init = found["package_init"]
    description = docstring_line(reading(init, tree.get(init, ""))) if init else ""
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


def propose(
    tree: dict[str, str], config: dict[str, Any]
) -> list[tuple[str, dict[str, Any]]]:
    read = readings(tree)
    index = ImportIndex(tree, read)
    scripts = project_scripts(tree)
    references = PathReferences(index, read, config)
    found = []
    for path in sorted(tree):
        if path not in read or not candidate_path(tree, path):
            continue
        facts = features(index, path, scripts, config)
        facts["referenced_by_path"] = references.referenced(path)
        if unused(facts):
            facts["proposer_score"] = proposer_score(facts)
            found.append((path, facts))
    return found


def wanted(path: str, kind: str, size: str) -> bool:
    name = PurePosixPath(path).name
    return (
        kind == "blob"
        and (proposed_source(path, READS) or name in MANIFESTS)
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
        for path, facts in propose(tree, config)
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
    except SourceError as error:
        return [
            Finding(
                RULE,
                ".",
                f"cannot read the project scripts: {error}",
                "repair the manifest so its project scripts can be read, then run "
                "consumer-candidates again",
            )
        ]
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
