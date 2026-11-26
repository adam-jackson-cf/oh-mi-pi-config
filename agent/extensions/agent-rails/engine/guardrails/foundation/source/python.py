"""Readers of Python syntax trees, and the engine's only uses of CPython's own parser.

The tree-sitter tree from tree.parse is the engine's Python parser. CPython's `ast` is used
here only: `literal_value` (only CPython decodes escapes, implicit concatenation and
number forms exactly), and `cpython_module` and `cpython_expression` for the
python_policy PYQ, PYS and TEST.subprocess-timeouts checks, which read expression contexts
(load or store) and compare `ast.unparse` text with configured annotations.
"""

from __future__ import annotations

import ast
import inspect
import warnings
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from guardrails.foundation.source.tree import (
    Definition,
    Offsets,
    SourceError,
    Span,
    definition,
    end_line,
    line,
    named,
    of_kinds,
    spans,
    unique_texts,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

IMPORT_KINDS = frozenset({
    "import_statement",
    "import_from_statement",
    "future_import_statement",
})
DOCUMENTED = frozenset({"function_definition", "class_definition"})
TEXT_KINDS = frozenset({"string", "concatenated_string"})
NON_TEXT_PREFIXES = frozenset("bBfFtT")
TRAILING_KINDS = frozenset({"comment", "line_continuation"})
IDENTIFIERS = frozenset({"identifier"})
CALLS = frozenset({"call"})
DYNAMIC_IMPORT_CALLS = frozenset({
    "importlib.import_module",
    "__import__",
    "builtins.__import__",
})


@dataclass(frozen=True)
class PythonImport:
    """One imported module. names are the original names a from-import takes ("*" for a
    wildcard); bindings are the names the statement binds in the importer."""

    statement: Literal["import", "from"]
    module: str
    names: tuple[str, ...]
    level: int
    line: int
    bindings: tuple[str, ...] = ()


def dotted(node: SgNode) -> str:
    return ".".join(part.text() for part in named(node) if part.kind() == "identifier")


def imported_name(node: SgNode) -> str:
    target = node.field("name") if node.kind() == "aliased_import" else node
    return dotted(target) if target is not None else ""


def binding(node: SgNode, *, first_part: bool) -> str:
    alias = node.field("alias") if node.kind() == "aliased_import" else None
    if alias is not None:
        return alias.text()
    name = imported_name(node)
    return name.partition(".")[0] if first_part else name


def from_import(node: SgNode) -> PythonImport:
    source = node.field("module_name")
    if source is None:
        module, level = "__future__", 0
    elif source.kind() == "relative_import":
        parts = named(source)
        prefix = next(part for part in parts if part.kind() == "import_prefix")
        path = [part for part in parts if part.kind() == "dotted_name"]
        module, level = (dotted(path[0]) if path else ""), len(prefix.text())
    else:
        module, level = dotted(source), 0
    items = node.field_children("name")
    names = tuple(imported_name(item) for item in items)
    bindings = tuple(binding(item, first_part=False) for item in items)
    wildcard = any(item.kind() == "wildcard_import" for item in named(node))
    if wildcard:
        names = bindings = ("*",)
    return PythonImport("from", module, names, level, line(node), bindings)


def imports(root: SgNode) -> list[PythonImport]:
    """Every import statement's modules, nested ones included, in source order."""
    found: list[PythonImport] = []
    for node in of_kinds(root, IMPORT_KINDS):
        if node.kind() == "import_statement":
            found.extend(
                PythonImport(
                    "import",
                    imported_name(item),
                    (),
                    0,
                    line(node),
                    (binding(item, first_part=True),),
                )
                for item in node.field_children("name")
            )
        else:
            found.append(from_import(node))
    return found


def qualified_names(found: list[PythonImport]) -> dict[str, str]:
    """The dotted name each import binding stands for: `import a.b` binds a to a,
    `import a.b as c` binds c to a.b, `from .m import f as g` binds g to .m.f."""
    names: dict[str, str] = {}
    for item in found:
        if item.statement == "import":
            bound = item.bindings[0]
            names[bound] = (
                bound if bound == item.module.partition(".")[0] else item.module
            )
            continue
        source = "." * item.level + (f"{item.module}." if item.module else "")
        names.update(
            (bound, source + name)
            for name, bound in zip(item.names, item.bindings, strict=True)
        )
    return names


def callee(node: SgNode) -> str:
    """The dotted name a call calls, or "" when it is not a plain or dotted name."""
    function = node.field("function")
    if function is None or function.kind() not in {"identifier", "attribute"}:
        return ""
    return ".".join(part.strip() for part in function.text().split("."))


def qualified(name: str, names: dict[str, str]) -> str:
    head, dot, rest = name.partition(".")
    return names.get(head, head) + dot + rest


def dynamic_imports(root: SgNode) -> list[tuple[str, PythonImport]]:
    """The calls that import a module named by a string literal (importlib.import_module,
    __import__), each with its callee as written; leading dots give a relative level."""
    names = qualified_names(imports(root))
    found: list[tuple[str, PythonImport]] = []
    for node in of_kinds(root, CALLS):
        name = callee(node)
        arguments = node.field("arguments")
        values = [] if arguments is None else named(arguments)
        first = values[0] if values else None
        if (
            qualified(name, names) not in DYNAMIC_IMPORT_CALLS
            or first is None
            or not text_literal(first)
        ):
            continue
        value = str(literal_value(first.text()))
        module = value.lstrip(".")
        found.append((
            name,
            PythonImport("import", module, (), len(value) - len(module), line(node)),
        ))
    return found


def last_token(node: SgNode) -> SgNode:
    while True:
        tail = [
            child for child in node.children() if child.kind() not in TRAILING_KINDS
        ]
        if not tail:
            return node
        node = tail[-1]


def defined(node: SgNode) -> SgNode:
    inner = node.field("definition") if node.kind() == "decorated_definition" else None
    return node if inner is None else inner


def named_definition(name: str, offsets: Offsets, node: SgNode) -> Definition:
    return definition(name, offsets, node, last_token(node))


def members(body: SgNode | None) -> list[SgNode]:
    """The function and class definitions directly in a module or class body."""
    if body is None:
        return []
    return [
        node
        for node in map(defined, named(body))
        if node.kind() in DOCUMENTED and node.field("name") is not None
    ]


def name_of(node: SgNode) -> str:
    name = node.field("name")
    return "" if name is None else name.text()


def decorators(node: SgNode) -> list[str]:
    """The decorator expressions of a definition, as source text without the @."""
    wrapper = node.parent()
    if wrapper is None or wrapper.kind() != "decorated_definition":
        return []
    return [
        named(item)[0].text()
        for item in named(wrapper)
        if item.kind() == "decorator" and named(item)
    ]


def definitions(root: SgNode, source: str) -> list[Definition]:
    offsets = Offsets(source)
    found: list[Definition] = []
    for node in members(root):
        name = node.field("name")
        if name is None:
            continue
        found.append(named_definition(name.text(), offsets, node))
        body = node.field("body")
        if node.kind() != "class_definition" or body is None:
            continue
        for member in map(defined, named(body)):
            method = member.field("name")
            if member.kind() == "function_definition" and method is not None:
                found.append(
                    named_definition(f"{name.text()}.{method.text()}", offsets, member)
                )
    return found


def text_literal(node: SgNode) -> bool:
    while node.kind() == "parenthesized_expression" and len(named(node)) == 1:
        node = named(node)[0]
    if node.kind() not in TEXT_KINDS:
        return False
    parts = named(node) if node.kind() == "concatenated_string" else [node]
    return all(
        part.kind() == "string"
        and not NON_TEXT_PREFIXES.intersection(named(part)[0].text().rstrip("'\""))
        for part in parts
    )


def docstring(body: SgNode) -> SgNode | None:
    statements = named(body)
    first = statements[0] if statements else None
    if first is None or first.kind() != "expression_statement":
        return None
    values = named(first)
    return first if len(values) == 1 and text_literal(values[0]) else None


def docstrings(root: SgNode) -> list[SgNode]:
    """The docstring statements of the module and of every function and class."""
    bodies = [root, *(node.field("body") for node in of_kinds(root, DOCUMENTED))]
    return [
        statement
        for body in bodies
        if body is not None and (statement := docstring(body)) is not None
    ]


def docstring_spans(root: SgNode, source: str) -> list[Span]:
    return spans(source, docstrings(root))


def literal_value(text: str) -> object:
    """CPython's value of the source text of a Python literal; SourceError when CPython
    rejects it (such as the truncated escape in \"\"\"\\x\"\"\")."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.literal_eval(f"(\n{text}\n)")
        except (SyntaxError, ValueError, TypeError, RecursionError) as error:
            raise SourceError(f"not a Python literal: {error}") from error


def docstring_text(statement: SgNode) -> str:
    """The cleaned text of a docstring statement, as inspect.getdoc gives it."""
    try:
        value = literal_value(named(statement)[0].text())
    except SourceError as error:
        raise SourceError(f"line {line(statement)}: docstring {error}") from error
    return inspect.cleandoc(str(value))


def body_docstring(body: SgNode | None) -> str:
    """The docstring text of a module, class or function body, or "" when it has none."""
    statement = None if body is None else docstring(body)
    return "" if statement is None else docstring_text(statement)


def statement_lines(node: SgNode) -> range:
    return range(line(node), end_line(node) + 1)


def cpython_module(path: str, source: str) -> ast.Module:
    """CPython's syntax tree of a Python file, for the python_policy checks only."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.parse(source, filename=path)
        except (SyntaxError, ValueError) as error:
            number = getattr(error, "lineno", None) or 1
            message = getattr(error, "msg", str(error))
            raise SourceError(
                f"line {number}: could not parse: {message}", number
            ) from error


def cpython_expression(source: str) -> ast.expr | None:
    """CPython's syntax tree of an expression, such as a string annotation, or None."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return ast.parse(source, mode="eval").body
        except (SyntaxError, ValueError):
            return None


def comment_spans(root: SgNode, source: str) -> list[Span]:
    return spans(source, root.find_all(kind="comment"))


def identifiers(root: SgNode) -> list[str]:
    return unique_texts(root, IDENTIFIERS)
