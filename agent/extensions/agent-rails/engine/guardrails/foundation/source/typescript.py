"""TypeScript and JavaScript source: imports and what they bind, string literals,
definitions, comments and identifiers, for every reader of these languages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from guardrails.foundation.source.tree import (
    Declaration,
    Definition,
    Offsets,
    definition,
    line,
    named,
    of_kinds,
)

if TYPE_CHECKING:
    from ast_grep_py import SgNode

Form = Literal[
    "named",
    "default",
    "namespace",
    "require",
    "dynamic",
    "export-from",
    "import-equals",
]
IMPORT_KINDS = frozenset({"import_statement", "export_statement", "call_expression"})
WRAPPER_KINDS = frozenset({"await_expression", "parenthesized_expression"})
ESCAPE = re.compile(
    r"\\(u\{[0-9A-Fa-f]+\}|u[0-9A-Fa-f]{4}|x[0-9A-Fa-f]{2}"
    r"|[0-3][0-7]{0,2}|[4-7][0-7]?|\r\n|[\s\S])"
)
SINGLE_ESCAPES = {
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}
LINE_TERMINATORS = frozenset({"\n", "\r", "\r\n", chr(0x2028), chr(0x2029)})
OCTAL_DIGITS = frozenset("01234567")
LAST_CODE_POINT = 0x10FFFF
FUNCTION_KINDS = frozenset({"function_declaration", "generator_function_declaration"})
CLASS_KINDS = frozenset({"class_declaration", "abstract_class_declaration"})
FUNCTION_VALUES = frozenset({
    "arrow_function",
    "function_expression",
    "generator_function",
})
SIGNATURE_KINDS = frozenset({"function_signature"})
TYPE_KINDS = CLASS_KINDS | {
    "interface_declaration",
    "enum_declaration",
    "type_alias_declaration",
}
NAMESPACE_KINDS = frozenset({"internal_module", "module"})
DECLARATION_WRAPPERS = frozenset({
    "export_statement",
    "ambient_declaration",
    "expression_statement",
})
DESTRUCTURING = frozenset({"object_pattern", "array_pattern", "rest_pattern"})
MEMBER_KINDS = {
    "method_definition": "method",
    "method_signature": "method",
    "abstract_method_signature": "method",
    "public_field_definition": "field",
    "field_definition": "field",
    "property_signature": "field",
    "property_identifier": "field",
    "enum_assignment": "field",
}


@dataclass(frozen=True)
class Binding:
    """One name an import binds: the export it names and the name it gets here.

    imported is the export's name ("default" for a default import), or None for the
    whole module (a namespace import, a require() or import() result, import x =
    require(), export *). local is the name bound in this file, or for export-from the
    name this file re-exports it as; None when no name is bound.
    """

    specifier: str
    imported: str | None
    local: str | None
    form: Form
    type_only: bool


@dataclass(frozen=True)
class ScriptImport:
    """One import or export-from statement, or one require() or import() call."""

    specifier: str
    line: int
    type_only: bool
    bindings: tuple[Binding, ...]


def code_point(escape: str) -> str:
    if escape.startswith("u{"):
        value = int(escape[2:-1], 16)
        if value > LAST_CODE_POINT:
            raise ValueError(f"\\{escape} is past the last Unicode code point")
        return chr(value)
    return chr(int(escape[1:], 16))


def unescaped(match: re.Match[str]) -> str:
    escape = match.group(1)
    if escape in LINE_TERMINATORS:
        return ""
    if escape in SINGLE_ESCAPES:
        return SINGLE_ESCAPES[escape]
    if escape[0] in OCTAL_DIGITS:
        return chr(int(escape, 8))
    return code_point(escape) if len(escape) > 1 else escape


def decoded(body: str) -> str | None:
    """A JavaScript string body's value; an unknown escape drops its backslash."""
    try:
        text = ESCAPE.sub(unescaped, body)
    except ValueError:
        return None
    return text.encode("utf-16-le", "surrogatepass").decode(
        "utf-16-le", "surrogatepass"
    )


def literal(node: SgNode | None) -> str | None:
    """The value of a string literal or of a template literal without substitutions."""
    if node is None:
        return None
    if node.kind() == "string":
        return decoded(node.text()[1:-1])
    if node.kind() != "template_string" or any(
        child.kind() == "template_substitution" for child in node.children()
    ):
        return None
    return decoded(node.text()[1:-1].replace("\r\n", "\n").replace("\r", "\n"))


def typed(node: SgNode) -> bool:
    return any(child.kind() == "type" for child in node.children())


def name_text(node: SgNode) -> str:
    value = literal(node) if node.kind() == "string" else None
    return node.text() if value is None else value


def specifier_bindings(
    node: SgNode, specifier: str, form: Form, type_only: bool
) -> list[Binding]:
    name, alias = node.field("name"), node.field("alias")
    if name is None:
        return []
    imported = name_text(name)
    local = imported if alias is None else name_text(alias)
    return [Binding(specifier, imported, local, form, type_only or typed(node))]


def clause_bindings(clause: SgNode, specifier: str, type_only: bool) -> list[Binding]:
    found: list[Binding] = []
    for item in named(clause):
        if item.kind() == "identifier":
            found.append(
                Binding(specifier, "default", item.text(), "default", type_only)
            )
        elif item.kind() == "namespace_import" and (names := named(item)):
            found.append(
                Binding(specifier, None, names[-1].text(), "namespace", type_only)
            )
        elif item.kind() == "named_imports":
            for entry in named(item):
                found.extend(specifier_bindings(entry, specifier, "named", type_only))
    return found


def export_bindings(node: SgNode, specifier: str, type_only: bool) -> list[Binding]:
    found: list[Binding] = []
    for item in named(node):
        if item.kind() == "namespace_export":
            names = named(item)
            local = names[-1].text() if names else None
            found.append(Binding(specifier, None, local, "export-from", type_only))
        elif item.kind() == "export_clause":
            for entry in named(item):
                found.extend(
                    specifier_bindings(entry, specifier, "export-from", type_only)
                )
            return found
    return found or [Binding(specifier, None, None, "export-from", type_only)]


def unwrapped(node: SgNode | None) -> SgNode | None:
    while node is not None and node.kind() in WRAPPER_KINDS:
        node = next(iter(named(node)), None)
    return node


def called_module(node: SgNode) -> str | None:
    if node.kind() != "call_expression":
        return None
    function = node.field("function")
    arguments = node.field("arguments")
    loads = function is not None and (
        function.kind() == "import"
        or (function.kind() == "identifier" and function.text() == "require")
    )
    values = named(arguments) if loads and arguments is not None else []
    return literal(values[0]) if values else None


def load_call(node: SgNode | None) -> SgNode | None:
    """The require() or import() call a value reads, through await and one member."""
    node = unwrapped(node)
    if node is not None and node.kind() == "member_expression":
        node = unwrapped(node.field("object"))
    return node if node is not None and called_module(node) is not None else None


def loaded_member(value: SgNode) -> str | None:
    node = unwrapped(value)
    prop = (
        node.field("property")
        if node is not None and node.kind() == "member_expression"
        else None
    )
    return None if prop is None else prop.text()


def same(first: SgNode | None, second: SgNode) -> bool:
    if first is None:
        return False
    one, other = first.range(), second.range()
    return (one.start.index, one.end.index) == (other.start.index, other.end.index)


def pattern_bindings(pattern: SgNode, specifier: str, form: Form) -> list[Binding]:
    found: list[Binding] = []
    for item in named(pattern):
        if item.kind() == "object_assignment_pattern":
            item = item.field("left") or item
        if item.kind() == "shorthand_property_identifier_pattern":
            found.append(Binding(specifier, item.text(), item.text(), form, False))
        elif item.kind() == "pair_pattern":
            key, value = item.field("key"), item.field("value")
            if key is not None and value is not None and value.kind() == "identifier":
                found.append(
                    Binding(specifier, name_text(key), value.text(), form, False)
                )
    return found


def declarator_of(call: SgNode) -> SgNode | None:
    node = call.parent()
    while node is not None and node.kind() in WRAPPER_KINDS | {"member_expression"}:
        node = node.parent()
    return node if node is not None and node.kind() == "variable_declarator" else None


def call_bindings(call: SgNode, specifier: str, form: Form) -> list[Binding]:
    declarator = declarator_of(call)
    value = declarator.field("value") if declarator is not None else None
    name = declarator.field("name") if declarator is not None else None
    if value is None or name is None or not same(load_call(value), call):
        return []
    member = loaded_member(value)
    if name.kind() == "identifier":
        return [Binding(specifier, member, name.text(), form, False)]
    if name.kind() == "object_pattern" and member is None:
        return pattern_bindings(name, specifier, form)
    return []


def call_site(node: SgNode) -> ScriptImport | None:
    specifier = called_module(node)
    if specifier is None:
        return None
    function = node.field("function")
    dynamic = function is not None and function.kind() == "import"
    form: Form = "dynamic" if dynamic else "require"
    bindings = tuple(call_bindings(node, specifier, form))
    return ScriptImport(specifier, line(node), False, bindings)


def statement_site(node: SgNode) -> ScriptImport | None:
    type_only = typed(node)
    clause = next(
        (item for item in named(node) if item.kind() == "import_require_clause"), None
    )
    specifier = literal(
        node.field("source") if clause is None else clause.field("source")
    )
    if specifier is None:
        return None
    if clause is not None:
        found = [
            Binding(specifier, None, item.text(), "import-equals", type_only)
            for item in named(clause)[:1]
            if item.kind() == "identifier"
        ]
    elif node.kind() == "import_statement":
        found = [
            binding
            for item in named(node)
            if item.kind() == "import_clause"
            for binding in clause_bindings(item, specifier, type_only)
        ]
    else:
        found = export_bindings(node, specifier, type_only)
    return ScriptImport(specifier, line(node), type_only, tuple(found))


def imports(root: SgNode) -> list[ScriptImport]:
    """Import and export-from statements, import x = require(), and require() and
    import() calls with a string specifier, in source order."""
    return [
        site
        for node in of_kinds(root, IMPORT_KINDS)
        if (
            site := call_site(node)
            if node.kind() == "call_expression"
            else statement_site(node)
        )
        is not None
    ]


def declared(statement: SgNode) -> SgNode | None:
    if statement.kind() == "export_statement":
        return statement.field("declaration")
    return statement


def function_values(node: SgNode, offsets: Offsets) -> list[Definition]:
    found = []
    for declarator in named(node):
        name, value = declarator.field("name"), declarator.field("value")
        if (
            name is not None
            and name.kind() == "identifier"
            and value is not None
            and value.kind() in FUNCTION_VALUES
        ):
            found.append(definition(name.text(), offsets, declarator, declarator))
    return found


def class_definitions(node: SgNode, offsets: Offsets) -> list[Definition]:
    name, body = node.field("name"), node.field("body")
    if name is None or body is None:
        return []
    keyword = next(child for child in node.children() if child.kind() != "decorator")
    found = [definition(name.text(), offsets, keyword, node)]
    for member in named(body):
        method = member.field("name")
        if member.kind() == "method_definition" and method is not None:
            found.append(
                definition(f"{name.text()}.{method.text()}", offsets, member, member)
            )
    return found


def definitions(root: SgNode, source: str) -> list[Definition]:
    offsets = Offsets(source)
    found: list[Definition] = []
    for statement in named(root):
        node = declared(statement)
        if node is None:
            continue
        name = node.field("name")
        if node.kind() in FUNCTION_KINDS and name is not None:
            found.append(definition(name.text(), offsets, node, node))
        elif node.kind() in CLASS_KINDS:
            found.extend(class_definitions(node, offsets))
        elif node.kind() == "lexical_declaration":
            found.extend(function_values(node, offsets))
    return found


def pattern_names(pattern: SgNode) -> list[SgNode]:
    """The identifiers a destructuring pattern binds (not its property keys or defaults)."""
    kind = pattern.kind()
    if kind in {"identifier", "shorthand_property_identifier_pattern"}:
        return [pattern]
    if kind == "pair_pattern":
        value = pattern.field("value")
        return [] if value is None else pattern_names(value)
    if kind in {"assignment_pattern", "object_assignment_pattern"}:
        left = pattern.field("left")
        return [] if left is None else pattern_names(left)
    if kind in DESTRUCTURING:
        return [name for item in named(pattern) for name in pattern_names(item)]
    return []


def variable_declarations(node: SgNode) -> list[Declaration]:
    return [
        Declaration(name.text(), "variable", declarator)
        for declarator in named(node)
        if declarator.kind() == "variable_declarator"
        and (target := declarator.field("name")) is not None
        for name in pattern_names(target)
    ]


def member_name(member: SgNode) -> SgNode | None:
    if member.kind() in {"property_identifier", "private_property_identifier"}:
        return member
    return member.field("name") or member.field("property")


def members(owner: str, body: SgNode | None) -> list[Declaration]:
    """The methods, fields, signatures and enum members a type body declares."""
    found = []
    for member in [] if body is None else named(body):
        kind = MEMBER_KINDS.get(member.kind())
        name = member_name(member) if kind is not None else None
        if kind is not None and name is not None:
            found.append(Declaration(name.text(), kind, member, owner))
    return found


def type_declarations(node: SgNode, name: str) -> list[Declaration]:
    kind = node.kind()
    if kind in CLASS_KINDS:
        return [Declaration(name, "class", node), *members(name, node.field("body"))]
    if kind == "type_alias_declaration":
        value = node.field("value")
        body = value if value is not None and value.kind() == "object_type" else None
        return [Declaration(name, "type_alias", node), *members(name, body)]
    return [Declaration(name, "type", node), *members(name, node.field("body"))]


def declaration(node: SgNode) -> SgNode | None:
    """The declaration a top-level statement holds: through export, declare and the
    expression statement a namespace sits in."""
    while node.kind() in DECLARATION_WRAPPERS:
        inner = (
            node.field("declaration")
            if node.kind() == "export_statement"
            else next(iter(named(node)), None)
        )
        if inner is None:
            return None
        node = inner
    return node


def declarations(root: SgNode) -> list[Declaration]:
    """Every top-level declaration, exported or not: functions, classes, interfaces,
    enums and type aliases with the members their bodies declare, namespaces, and the
    names variable declarations bind; imports declare nothing here."""
    found: list[Declaration] = []
    for statement in named(root):
        node = declaration(statement)
        if node is None:
            continue
        kind = node.kind()
        name = node.field("name")
        if kind in {"lexical_declaration", "variable_declaration"}:
            found.extend(variable_declarations(node))
        elif name is None:
            continue
        elif kind in FUNCTION_KINDS | SIGNATURE_KINDS:
            found.append(Declaration(name.text(), "function", node))
        elif kind in TYPE_KINDS:
            found.extend(type_declarations(node, name.text()))
        elif kind in NAMESPACE_KINDS:
            found.append(Declaration(name.text(), "namespace", node))
    return found


def bound_names(root: SgNode) -> list[str]:
    return list(dict.fromkeys(item.name for item in declarations(root)))
