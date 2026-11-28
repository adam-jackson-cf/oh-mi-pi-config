"""The definitions and alias bindings name-pairs compares, read through the shared
source readers (foundation/source): each proposed language's top-level definitions,
methods, constants and aliases, their docstrings or doc comments, and the 3-grams of
their code with comments and docstrings removed."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from guardrails.commands.structure_naming.scan_scope import runs
from guardrails.commands.structure_naming.tokens import cleaned, removed_spans
from guardrails.foundation import conventions
from guardrails.foundation.source import comments, go, names, python, typescript
from guardrails.foundation.source.tree import (
    Declaration,
    SourceError,
    end_line,
    language_of,
    line,
    named,
    parse,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

SKIPPED_MODULES = frozenset({
    "__init__",
    "__main__",
    "conftest",
    "index",
    "lib",
    "main",
    "mod",
    "setup",
})
UPPER = re.compile(r"^_?[A-Z][A-Z0-9_]*$")
CAPITALISED = re.compile(r"^_?[A-Z][a-z0-9]+[A-Za-z0-9]*$")
IDENTIFIER = re.compile(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$")
TOKEN = re.compile(r"[A-Za-z_$][\w$]*|\S")
CODE_LINES = 30
DOC_LINES = 8
PYTHON_VALUES = frozenset({"identifier", "attribute"})
PYTHON_TYPES = frozenset({"subscript", "binary_operator"})
SCRIPT_FUNCTIONS = frozenset({
    "arrow_function",
    "function_expression",
    "generator_function",
})
SCRIPT_VALUES = frozenset({"identifier", "member_expression"})
GO_VALUES = frozenset({"identifier", "selector_expression"})
GO_GROUPS = frozenset({"const_declaration", "var_declaration", "type_declaration"})
KEYWORDS = frozenset({"keyword_argument", "dictionary_splat"})
EXPORTS = frozenset({"export_statement", "ambient_declaration"})
GO_FUNCTIONS = {"function_declaration": "function", "method_declaration": "method"}

Gram = tuple[str, str, str]


def stem(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def words(name: str, keep_dots: bool = False) -> list[str]:
    if not keep_dots and "." in name and not name.startswith('"'):
        name = name.split(".")[-1]
    return [stem(word) for word in names.words(name)]


def flat(name: str) -> str:
    return name.split(".")[-1].lstrip("_").lower().replace("_", "")


def grams(code: str) -> frozenset[Gram]:
    tokens = TOKEN.findall(code)
    return frozenset(zip(tokens, tokens[1:], tokens[2:], strict=False))


def body_code(place: Place, start: int, end: int) -> str:
    return "\n".join(place.code[start - 1 : end])


def first_lines(lines: list[str], start: int, end: int) -> str:
    text = "\n".join(lines[start - 1 : min(end, start + CODE_LINES - 1)])
    return text + "\n# ... (truncated)" if end > start + CODE_LINES - 1 else text


@dataclass(frozen=True)
class Place:
    """A file being read: its path, language, source lines and the same lines with
    comments and docstrings blanked (the code every language's 3-grams read)."""

    path: str
    language: str
    lines: list[str]
    code: list[str]


@dataclass(frozen=True)
class Detail:
    signature: str = ""
    doc: str = ""
    owner: str | None = None
    value: str | None = None
    top: bool = True
    bases: tuple[str, ...] = ()


@dataclass
class Found:
    definitions: list[dict[str, Any]] = field(default_factory=list)
    aliases: list[dict[str, Any]] = field(default_factory=list)


def definition(
    place: Place, name: str, kind: str, span: tuple[int, int], detail: Detail
) -> dict[str, Any]:
    start, end = span
    return {
        "name": name,
        "base": name.split(".")[-1],
        "kind": kind,
        "lang": place.language,
        "path": place.path,
        "line": start,
        "end": end,
        "sig": detail.signature.strip()[:300],
        "doc": (detail.doc or "").strip()[:600],
        "owner": detail.owner,
        "value": detail.value,
        "top": detail.top,
        "bases": list(detail.bases),
        "code": first_lines(place.lines, start, end),
        "nlines": end - start + 1,
        "grams": grams(body_code(place, start, end)),
    }


def module_definition(place: Place, doc: str) -> list[dict[str, Any]]:
    name = PurePosixPath(place.path).name.rsplit(".", 1)[0]
    if place.language != "python":
        name = re.sub(r"\.(test|spec)$", "", name)
    if name in SKIPPED_MODULES:
        return []
    found = definition(
        place,
        name,
        "module",
        (1, len(place.lines)),
        Detail(signature="module " + place.path, doc=doc),
    )
    found["grams"] = frozenset()
    return [found]


def alias(
    place: Place, names: tuple[str, str], at: int, text: str, how: str
) -> dict[str, Any]:
    bound, target = names
    return {
        "alias": bound,
        "alias_base": bound.split(".")[-1],
        "target": target,
        "path": place.path,
        "line": at,
        "text": text,
        "how": how,
        "internal": None,
    }


def python_paths(paths: list[str]) -> list[str]:
    return [path for path in paths if language_of(path) == "python"]


def module_roots(paths: list[str]) -> set[str]:
    roots: set[str] = set()
    for path in python_paths(paths):
        parts = path.split("/")
        roots.add(parts[0].rsplit(".", 1)[0] if len(parts) == 1 else parts[0])
        if parts[0] in {"src", "lib", "packages"} and len(parts) > 1:
            roots.add(parts[1].rsplit(".", 1)[0])
        roots.update(parts[:-1])
    return roots


def dotted_text(node: SgNode) -> str:
    """A name or attribute chain as written without spacing (a.b.c), else its text."""
    if node.kind() == "attribute":
        owner, attribute = node.field("object"), node.field("attribute")
        if owner is not None and attribute is not None:
            return f"{dotted_text(owner)}.{attribute.text()}"
    return node.text()


def unparenthesized(node: SgNode) -> SgNode:
    while node.kind() == "parenthesized_expression" and len(named(node)) == 1:
        node = named(node)[0]
    return node


class PythonSource:
    def __init__(self, place: Place, roots: set[str]) -> None:
        self.place, self.roots, self.found = place, roots, Found()

    def segment(self, first: int, last: int) -> str:
        return "\n".join(self.place.lines[first - 1 : last])

    def signature(self, node: SgNode) -> tuple[int, str]:
        """The first line of a definition (its first decorator) and its header: the
        lines from the def or class keyword up to its body's first statement."""
        wrapper = node.parent()
        decorated = wrapper is not None and wrapper.kind() == "decorated_definition"
        start = line(wrapper if wrapper is not None and decorated else node)
        body = node.field("body")
        statements = [] if body is None else named(body)
        first = python.defined(statements[0]) if statements else None
        opens = line(first) if first is not None else line(node)
        if opens > line(node):
            return start, self.segment(line(node), opens - 1)
        return start, self.place.lines[line(node) - 1]

    def add(self, name: str, kind: str, span: tuple[int, int], detail: Detail) -> None:
        self.found.definitions.append(definition(self.place, name, kind, span, detail))

    def end(self, node: SgNode) -> int:
        return end_line(python.last_token(node))

    def function(self, node: SgNode) -> None:
        start, signature = self.signature(node)
        doc = python.body_docstring(node.field("body"))
        detail = Detail(signature, doc)
        self.add(python.name_of(node), "function", (start, self.end(node)), detail)

    def klass(self, node: SgNode) -> None:
        start, signature = self.signature(node)
        bases = node.field("superclasses")
        found = tuple(
            "".join(base.text().split()).split(".")[-1].split("[")[0]
            for base in ([] if bases is None else named(bases))
            if base.kind() not in KEYWORDS
        )
        name = python.name_of(node)
        doc = python.body_docstring(node.field("body"))
        detail = Detail(signature, doc, bases=found)
        self.add(name, "class", (start, self.end(node)), detail)
        body = node.field("body")
        for item in [] if body is None else named(body):
            member = python.defined(item)
            if member.kind() == "function_definition":
                self.method(name, member)
            elif (assigned := assignment_of(member)) is not None:
                self.class_alias(name, member, assigned)

    def method(self, owner: str, node: SgNode) -> None:
        name = python.name_of(node)
        if name.startswith("__") and name.endswith("__"):
            return
        begin, text = self.signature(node)
        doc = python.body_docstring(node.field("body"))
        detail = Detail(text, doc, owner=owner, top=False)
        self.add(f"{owner}.{name}", "method", (begin, self.end(node)), detail)

    def class_alias(self, owner: str, statement: SgNode, node: SgNode) -> None:
        if node.field("type") is not None:
            return
        target, value = single_target(node)
        if target is None or value is None or value.kind() not in PYTHON_VALUES:
            return
        bound, aimed = target.text(), dotted_text(value)
        if bound.startswith("__") or bound == aimed.split(".")[-1]:
            return
        text = self.place.lines[line(statement) - 1].strip()
        found = alias(
            self.place,
            (f"{owner}.{bound}", aimed),
            line(statement),
            text,
            "class_assign",
        )
        found.update(alias_base=bound, internal=True, owner=owner)
        self.found.aliases.append(found)

    def assignment(self, statement: SgNode, node: SgNode) -> None:
        target, value = single_target(node)
        if target is None or value is None:
            return
        bound = target.text()
        if bound.startswith("__"):
            return
        if value.kind() in PYTHON_VALUES:
            aimed = dotted_text(value)
            if bound != aimed.split(".")[-1]:
                text = self.segment(line(statement), end_line(statement))[:300]
                self.found.aliases.append(
                    alias(self.place, (bound, aimed), line(statement), text, "assign")
                )
            return
        signature = self.place.lines[line(statement) - 1]
        span = (line(statement), end_line(statement))
        if UPPER.match(bound):
            held = (
                str(python.literal_value(value.text()))
                if python.text_literal(value)
                else None
            )
            self.add(bound, "constant", span, Detail(signature, value=held))
        elif CAPITALISED.match(bound) and unparenthesized(value).kind() in PYTHON_TYPES:
            self.add(bound, "type", span, Detail(signature))

    def internal(self, module: str, level: int) -> bool:
        return level > 0 or (bool(module) and module.split(".")[0] in self.roots)

    def import_from(self, node: SgNode) -> None:
        found = python.from_import(node)
        if not self.internal(found.module, found.level):
            return
        text = self.place.lines[line(node) - 1].strip()[:300]
        for name, bound in zip(found.names, found.bindings, strict=True):
            if bound != name and name != "*":
                item = alias(self.place, (bound, name), line(node), text, "import_as")
                item.update(internal=True, module="." * found.level + found.module)
                self.found.aliases.append(item)

    def import_module(self, node: SgNode) -> None:
        text = self.place.lines[line(node) - 1].strip()[:300]
        for item in node.field_children("name"):
            given = item.field("alias") if item.kind() == "aliased_import" else None
            module = python.imported_name(item)
            last = module.split(".")[-1]
            if (
                given is not None
                and module.split(".")[0] in self.roots
                and given.text() != last
            ):
                found = alias(
                    self.place,
                    (given.text(), last),
                    line(node),
                    text,
                    "import_module_as",
                )
                found["internal"] = True
                self.found.aliases.append(found)

    def extract(self, root: SgNode) -> Found:
        self.found.definitions.extend(
            module_definition(self.place, python.body_docstring(root))
        )
        for statement in named(root):
            node = python.defined(statement)
            kind = node.kind()
            if kind == "function_definition":
                self.function(node)
            elif kind == "class_definition":
                self.klass(node)
            elif kind == "import_from_statement":
                self.import_from(node)
            elif kind == "import_statement":
                self.import_module(node)
            elif (assigned := assignment_of(node)) is not None:
                self.assignment(node, assigned)
        return self.found


def assignment_of(statement: SgNode) -> SgNode | None:
    """The plain or annotated assignment an expression statement is."""
    if statement.kind() != "expression_statement":
        return None
    values = named(statement)
    if len(values) != 1 or values[0].kind() != "assignment":
        return None
    return values[0]


def single_target(node: SgNode) -> tuple[SgNode | None, SgNode | None]:
    """The one name an assignment binds and the value it binds it to: (None, None) for
    a chain (a = b = 1), several targets or an attribute or subscript target."""
    target, value = node.field("left"), node.field("right")
    if target is None or value is None or value.kind() == "assignment":
        return None, None
    target = unparenthesized(target)
    if target.kind() != "identifier":
        return None, None
    return target, unparenthesized(value)


def doc_above(place: Place, node: SgNode) -> str:
    """The comment lines directly above a declaration, without their markers."""
    syntax = comments.markers(conventions.comment_syntax(place.path))
    holder = node.parent()
    while holder is not None and holder.kind() in EXPORTS:
        node, holder = holder, holder.parent()
    found: list[str] = []
    item, start = node.prev(), line(node)
    while item is not None and item.kind() == "comment" and len(found) < DOC_LINES:
        if end_line(item) < start - 1:
            break
        text = comments.content(item.text(), syntax)
        found[:0] = [part.strip().lstrip("*").strip() for part in text.split("\n")]
        start = line(item)
        item = item.prev()
    parent = node.parent()
    if not found and parent is not None and parent.kind() in GO_GROUPS:
        specs = named(parent)
        if specs and specs[0] == node:
            return doc_above(place, parent)
    return " ".join(part for part in found if part).strip()


class DeclaredSource:
    """TypeScript and Go: the definitions their declarations readers name."""

    def __init__(self, place: Place) -> None:
        self.place, self.found = place, Found()

    def add(self, item: Declaration, kind: str, detail: Detail = Detail()) -> None:
        node = item.node
        name = f"{item.owner}.{item.name}" if item.owner else item.name
        detail = Detail(
            self.place.lines[line(node) - 1],
            doc_above(self.place, node),
            item.owner,
            detail.value,
            item.owner is None,
            detail.bases,
        )
        span = (line(node), end_line(node))
        self.found.definitions.append(definition(self.place, name, kind, span, detail))

    def bind(self, names: tuple[str, str], node: SgNode, how: str) -> None:
        text = self.place.lines[line(node) - 1].strip()
        found = alias(self.place, names, line(node), text, how)
        self.found.aliases.append(found)

    def value_binding(self, item: Declaration, value: SgNode | None) -> bool:
        """Whether a constant or variable binds another name (an alias)."""
        if value is None or value.kind() not in GO_VALUES | SCRIPT_VALUES:
            return False
        target = "".join(value.text().split())
        if not IDENTIFIER.match(target) or target.split(".")[-1] == item.name:
            return False
        self.bind((item.name, target), item.node, "assign")
        return True

    def type_alias(self, item: Declaration, value: SgNode | None) -> None:
        target = "" if value is None else value.text().lstrip("*")
        if IDENTIFIER.match(target) and target.split(".")[-1] != item.name:
            self.bind((item.name, target), item.node, "type_alias")
        else:
            self.add(item, "class" if self.place.language == "go" else "type")


def go_value(spec: SgNode, name: str) -> SgNode | None:
    names = [item.text() for item in spec.field_children("name")]
    values = spec.field("value")
    items = [] if values is None else named(values)
    return items[names.index(name)] if len(items) == len(names) else None


class GoSource(DeclaredSource):
    def extract(self, root: SgNode) -> Found:
        clause = next(iter(named(root)), None)
        doc = "" if clause is None else doc_above(self.place, clause)
        self.found.definitions.extend(module_definition(self.place, doc))
        for item in go.declarations(root):
            if item.node.kind() in GO_FUNCTIONS:
                self.add(item, GO_FUNCTIONS[item.node.kind()])
            elif item.kind == "type":
                self.add(item, "class")
            elif item.kind == "type_alias":
                value = item.node.field("type")
                self.type_alias(item, None if value is None else value)
            elif item.kind in {"constant", "variable"}:
                value = go_value(item.node, item.name)
                if value is not None and not self.value_binding(item, value):
                    held = go.string_value(value)
                    self.add(item, "constant", Detail(value=held))
        return self.found


class ScriptSource(DeclaredSource):
    def extract(self, root: SgNode) -> Found:
        statements = named(root)
        doc = doc_above(self.place, statements[0]) if statements else ""
        self.found.definitions.extend(module_definition(self.place, doc))
        sites = typescript.imports(root)
        # The names this file imports: a binding of one resolves through its specifier.
        self.imported = {
            binding.local: binding
            for site in sites
            for binding in site.bindings
            if binding.local is not None and binding.form != "export-from"
        }
        for item in typescript.declarations(root):
            self.declared(item)
        for site in sites:
            self.renames(site)
        return self.found

    def bind(self, names: tuple[str, str], node: SgNode, how: str) -> None:
        """A binding of an imported name aims at the export it imports, through the
        import's specifier; any other binding resolves by name."""
        local, target = names
        parts = target.split(".")
        source = self.imported.get(parts[0])
        if source is None:
            super().bind(names, node, how)
            return
        export = [] if source.imported in {None, "default"} else [source.imported]
        aimed = ".".join([*export, *parts[1:]]) or target
        text = self.place.lines[line(node) - 1].strip()
        found = alias(self.place, (local, aimed), line(node), text, how)
        found.update(internal=True, specifier=source.specifier)
        self.found.aliases.append(found)

    def declared(self, item: Declaration) -> None:
        node = item.node
        if item.kind == "function" or node.kind() == "method_definition":
            self.add(item, item.kind)
        elif item.kind == "class":
            heritage = next(
                (child for child in named(node) if child.kind() == "class_heritage"),
                None,
            )
            extends = next(
                (
                    named(clause)[0].text().split(".")[-1]
                    for clause in ([] if heritage is None else named(heritage))
                    if clause.kind() == "extends_clause" and named(clause)
                ),
                None,
            )
            self.add(item, "class", Detail(bases=() if extends is None else (extends,)))
        elif item.kind == "type" and item.owner is None:
            self.add(item, "type")
        elif item.kind == "type_alias":
            self.type_alias(item, node.field("value"))
        elif item.kind == "variable" and node.field("name") is not None:
            self.variable(item, node)

    def variable(self, item: Declaration, declarator: SgNode) -> None:
        name, value = declarator.field("name"), declarator.field("value")
        if name is None or name.kind() != "identifier" or value is None:
            return
        if value.kind() in SCRIPT_FUNCTIONS:
            self.add(item, "function")
        elif not self.value_binding(item, value) and UPPER.match(item.name):
            self.add(item, "constant", Detail(value=typescript.literal(value)))

    def renames(self, site: typescript.ScriptImport) -> None:
        for binding in site.bindings:
            imported, local = binding.imported, binding.local
            if imported in {None, "default", local} or local is None:
                continue
            if binding.form == "named" and site.specifier.startswith("."):
                how = "import_as"
            elif binding.form == "export-from":
                how = "reexport_as"
            else:
                continue
            text = self.place.lines[site.line - 1].strip()[:300]
            found = alias(self.place, (local, str(imported)), site.line, text, how)
            # The specifier the rename imports from: its target resolves through it.
            found.update(internal=True, specifier=site.specifier)
            self.found.aliases.append(found)


Reader = Callable[[Place, set[str], "SgNode"], Found]


def read_python(place: Place, roots: set[str], root: SgNode) -> Found:
    return PythonSource(place, roots).extract(root)


def read_go(place: Place, roots: set[str], root: SgNode) -> Found:
    return GoSource(place).extract(root)


def read_script(place: Place, roots: set[str], root: SgNode) -> Found:
    return ScriptSource(place).extract(root)


READERS: dict[str, Reader] = {
    "python": read_python,
    "typescript": read_script,
    "go": read_go,
}


def extracted(path: str, text: str, roots: set[str]) -> Found:
    """One file's definitions and aliases; a file that does not parse, or whose
    docstrings or string constants CPython rejects, gives none."""
    language = str(language_of(path))
    try:
        root = parse(path, text)
        code = cleaned(text, removed_spans(language, root, text)).decode(
            errors="replace"
        )
        place = Place(path, language, text.split("\n"), code.split("\n"))
        return READERS[language](place, roots, root)
    except SourceError:
        return Found()


def extract_tree(
    tree: dict[str, str], is_test: Callable[[str], bool], languages: Collection[str]
) -> Found:
    """The definitions and aliases of every source file that runs in one of the
    languages, test source excepted."""
    sources = {path: text for path, text in tree.items() if runs(path, languages)}
    roots = module_roots(list(sources))
    found = Found()
    for path in sorted(sources):
        if is_test(path):
            continue
        part = extracted(path, sources[path], roots)
        found.definitions.extend(part.definitions)
        found.aliases.extend(part.aliases)
    return found
