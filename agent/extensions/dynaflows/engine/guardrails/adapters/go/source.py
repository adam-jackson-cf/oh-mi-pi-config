from __future__ import annotations

import posixpath
import re
import shlex
import shutil
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.structure.inventory import settings

if TYPE_CHECKING:
    from ast_grep_py import SgNode

NAME = "go"
SETTINGS = "adapter_go"
SOURCE_SUFFIX = ".go"
TEST_SUFFIX = "_test.go"
SKIPPED_PARTS = frozenset({".git", "vendor", "testdata", "node_modules"})
TEST_NAME = re.compile(r"^(Test|Fuzz|Example)(?![a-z])\w*$")
TEST_DEFINITION = re.compile(r"^func\s+((?:Test|Fuzz|Example)(?![a-z])\w*)\s*\(", re.M)
MODULE_LINE = re.compile(r"^module\s+(\S+)", re.M)
VERSION_ELEMENT = re.compile(r"^v\d+$")
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


class SourceError(ValueError):
    pass


def parse(text: str) -> SgNode:
    from ast_grep_py import SgRoot

    return SgRoot(text, "go").root()


def has_errors(root: SgNode) -> bool:
    return root.find(kind="ERROR") is not None


def children(node: SgNode) -> list[SgNode]:
    return [
        item for item in node.children() if item.is_named() and item.kind() != "comment"
    ]


def walk(node: SgNode) -> Iterator[SgNode]:
    yield node
    for item in children(node):
        yield from walk(item)


def string_value(node: SgNode | None) -> str | None:
    if node is None:
        return None
    if node.kind() in {"interpreted_string_literal", "raw_string_literal"}:
        return node.text()[1:-1]
    return None


def default_alias(path: str) -> str:
    elements = path.split("/")
    name = elements[-1]
    if VERSION_ELEMENT.match(name) and len(elements) > 1:
        name = elements[-2]
    name = name.split(".")[0].removeprefix("go-").removesuffix("-go")
    return name.replace("-", "")


def imports(root: SgNode) -> dict[str, str]:
    found: dict[str, str] = {}
    for node in walk(root):
        if node.kind() != "import_spec":
            continue
        path = string_value(node.field("path"))
        if path is None:
            continue
        name = node.field("name")
        alias = name.text() if name is not None else default_alias(path)
        if alias not in {".", "_"}:
            found[alias] = path
    return found


def words(name: str) -> list[str]:
    return [item.lower() for item in WORD.findall(name)]


def test_functions(root: SgNode) -> list[SgNode]:
    return [
        node
        for node in children(root)
        if node.kind() == "function_declaration"
        and (name := node.field("name")) is not None
        and TEST_NAME.match(name.text())
    ]


def first_parameter(function: SgNode) -> str | None:
    parameters = function.field("parameters")
    for declaration in children(parameters) if parameters is not None else []:
        name = declaration.field("name")
        if name is not None:
            return name.text()
    return None


def call_name(call: SgNode) -> tuple[str | None, str | None]:
    function = call.field("function")
    if function is None:
        return None, None
    if function.kind() == "selector_expression":
        operand, field = function.field("operand"), function.field("field")
        return (
            operand.text() if operand is not None else None,
            field.text() if field is not None else None,
        )
    if function.kind() == "identifier":
        return None, function.text()
    return None, None


def arguments(call: SgNode) -> list[SgNode]:
    node = call.field("arguments")
    return children(node) if node is not None else []


def is_test_path(path: str) -> bool:
    return path.endswith(TEST_SUFFIX)


def go_files(paths: list[str]) -> list[str]:
    return sorted({
        path
        for path in paths
        if path.endswith(SOURCE_SUFFIX)
        and not SKIPPED_PARTS.intersection(PurePosixPath(path).parts)
    })


def module_of(repo: Path, path: str) -> tuple[str, str] | None:
    folder = PurePosixPath(path).parent
    while True:
        manifest = repo / folder / "go.mod"
        if manifest.is_file():
            match = MODULE_LINE.search(manifest.read_text(encoding="utf-8"))
            if match is None:
                raise SourceError(f"{manifest} names no module")
            return folder.as_posix(), match.group(1)
        if folder == PurePosixPath("."):
            return None
        folder = folder.parent


def owned_roots(profile: dict[str, Any]) -> tuple[str, ...]:
    entries = profile.get("public_entry_points")
    roots = set()
    for raw in entries if isinstance(entries, dict) else ():
        path = str(raw).replace("\\", "/").rstrip("/")
        if path.endswith(SOURCE_SUFFIX):
            path = posixpath.dirname(path) or "."
        roots.add(posixpath.normpath(path))
    return tuple(sorted(roots))


def under(path: str, root: str) -> bool:
    return root == "." or path == root or path.startswith(root + "/")


def owned_imports(repo: Path, profile: dict[str, Any]) -> tuple[str, ...]:
    prefixes = []
    for root in owned_roots(profile):
        module = module_of(repo, posixpath.join(root, "x.go"))
        if module is None:
            continue
        folder, name = module
        relative = posixpath.relpath(root, folder)
        prefixes.append(name if relative == "." else f"{name}/{relative}")
    return tuple(sorted(prefixes))


def owned_import(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


def owned_path(path: str, profile: dict[str, Any]) -> bool:
    return any(under(path, root) for root in owned_roots(profile))


def command(profile: dict[str, Any]) -> list[str]:
    configured = settings(profile, SETTINGS).get("command")
    if configured is not None:
        if (
            not isinstance(configured, list)
            or not configured
            or not all(isinstance(part, str) for part in configured)
        ):
            raise SourceError(
                f"guardrails.{SETTINGS}.command must be a nonempty argv list"
            )
        return configured
    go = shutil.which("go")
    if go is None:
        raise SourceError(
            f"go is not on PATH; activate the repository's Go or record {SETTINGS}.command"
        )
    return [go]


def selector_arguments(selector: str) -> list[str]:
    return shlex.split(selector)


def package_patterns(locations: list[str]) -> list[str]:
    patterns = []
    for location in locations:
        cleaned = location.strip().rstrip("/") or "."
        if cleaned == "." or cleaned.startswith(("./", "../", "/")):
            patterns.append(cleaned if cleaned != "." else "./")
        else:
            patterns.append(f"./{cleaned}")
    return patterns


def defined_tests(text: str) -> list[str]:
    return TEST_DEFINITION.findall(text)
