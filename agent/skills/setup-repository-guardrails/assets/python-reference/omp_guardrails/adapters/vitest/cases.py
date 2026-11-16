"""Static Vitest collection; never imports or executes the target's JavaScript.

Selectors use the shared ``path::suite::test[row]`` convention. Dynamic registration
is rejected rather than silently losing cases from growth and duplicate checks.
"""
from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass


class CollectionError(ValueError):
    """The source cannot be collected without executing JavaScript."""


@dataclass(frozen=True)
class Case:
    selector: str
    fingerprint: str


@dataclass(frozen=True)
class Collected:
    cases: tuple[Case, ...]
    behaviours: Mapping[str, str | None]


def children(node):
    return [item for item in node.children() if item.is_named() and item.kind() != "comment"]


def walk(node):
    yield node
    for item in children(node):
        yield from walk(item)


def parse(path: str, text: str):
    from ast_grep_py import SgRoot

    root = SgRoot(text, "tsx" if path.endswith((".tsx", ".jsx")) else "typescript").root()
    if any(node.kind() == "ERROR" for node in walk(root)):
        raise CollectionError(f"invalid JavaScript/TypeScript in {path}")
    return root


def string(node) -> str | None:
    if node is None or node.kind() not in {"string", "template_string"}:
        return None
    text = node.text()
    if text.startswith("`"):
        return text[1:-1] if "${" not in text else None
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def imports(root) -> dict[str, str]:
    """Local binding -> module#export, retaining namespace member resolution."""
    found = {}
    for statement in root.find_all({"rule": {"kind": "import_statement"}}):
        module = string(statement.field("source"))
        if module is None:
            continue
        for node in walk(statement):
            if node.kind() == "import_specifier":
                name, alias = node.field("name"), node.field("alias")
                if name is not None:
                    found[(alias or name).text()] = f"{module}#{name.text()}"
            elif node.kind() == "namespace_import":
                names = children(node)
                if names:
                    found[names[-1].text()] = module
            elif node.kind() == "import_clause":
                for child in children(node):
                    if child.kind() == "identifier":
                        found[child.text()] = module if module == "fast-check" else f"{module}#default"
    return found


def arguments(call):
    node = call.field("arguments")
    return children(node) if node is not None else []


def qualified(node, aliases: Mapping[str, str]) -> str:
    if node is None:
        return ""
    if node.kind() == "identifier":
        return aliases.get(node.text(), node.text())
    if node.kind() in {"member_expression", "subscript_expression"}:
        obj = qualified(node.field("object"), aliases)
        prop = node.field("property") or node.field("index")
        member = string(prop) or (prop.text() if prop is not None else "")
        return f"{obj}.{member}"
    return ""


def api_name(node, aliases):
    name = qualified(node, aliases)
    for prefix in ("vitest#", "vitest."):
        if name.startswith(prefix):
            return name[len(prefix):]
    return name


def literal(node, constants, active=frozenset()):
    if node.kind() == "identifier" and node.text() in constants:
        if node.text() in active:
            raise CollectionError("cyclic static parameter constant")
        return literal(constants[node.text()], constants, active | {node.text()})
    value = string(node)
    if value is not None:
        return value
    if node.kind() in {"number", "true", "false", "null", "unary_expression"}:
        try:
            return json.loads(node.text())
        except ValueError:
            pass
    if node.kind() == "array":
        return [literal(item, constants, active) for item in children(node)]
    if node.kind() == "object":
        result = {}
        for pair in children(node):
            key, value = pair.field("key"), pair.field("value")
            if key is None or value is None:
                raise CollectionError("parameter objects require static key/value pairs")
            result[string(key) or key.text()] = literal(value, constants, active)
        return result
    raise CollectionError(f"dynamic parameter value: {node.text()[:80]}")


def fingerprint(callback, aliases, bindings):
    """Normalise AST structure, imported aliases, parameter values and local names."""
    local = {}
    for node in walk(callback):
        if node.kind() == "variable_declarator":
            name = node.field("name")
            if name is not None and name.kind() == "identifier":
                local.setdefault(name.text(), f"local{len(local)}")
    parameters = callback.field("parameters") or callback.field("parameter")
    if parameters is not None:
        for node in walk(parameters):
            if node.kind() == "identifier":
                local.setdefault(node.text(), f"arg{len(local)}")
    temporaries = {}
    nodes = list(walk(callback))
    for node in nodes:
        if node.kind() != "variable_declarator":
            continue
        name, value = node.field("name"), node.field("value")
        parent = node.parent()
        if name is None or value is None or name.kind() != "identifier":
            continue
        if parent is None or len(children(parent)) != 1:
            continue
        occurrences = [item for item in nodes if item.kind() == "identifier" and item.text() == name.text()]
        if len(occurrences) == 2 and not any(
            item.kind() in {"assignment_expression", "update_expression"} and name.text() in item.text()
            for item in nodes
        ):
            temporaries[name.text()] = value

    def normalized(node):
        kind = node.kind()
        if kind in {"comment", "type_annotation"}:
            return None
        if kind in {"lexical_declaration", "variable_declaration"}:
            declarations = children(node)
            if declarations and all(
                item.kind() == "variable_declarator"
                and item.field("name") is not None
                and item.field("name").text() in temporaries
                for item in declarations
            ):
                return None
        if kind == "member_expression":
            origin = qualified(node, aliases)
            head = qualified(node.field("object"), aliases)
            if head and any(head.startswith(module) for module in aliases.values()):
                return ["identifier", origin.replace("#", ".").removeprefix("vitest.")]
        if kind == "identifier":
            name = node.text()
            if name in bindings:
                return ["literal", bindings[name]]
            if name in temporaries:
                value = temporaries.pop(name)
                result = normalized(value)
                temporaries[name] = value
                return result
            origin = aliases.get(name, name)
            if origin.startswith("vitest#"):
                origin = origin.removeprefix("vitest#")
            return [kind, local.get(name, origin.replace("#", "."))]
        value = string(node)
        if value is not None:
            return ["literal", value]
        if kind in {"number", "true", "false", "null", "unary_expression"}:
            try:
                return ["literal", json.loads(node.text())]
            except ValueError:
                pass
        parts = []
        for child in node.children():
            if child.kind() in {"comment", "type_annotation"} or child.text() == ";":
                continue
            value = normalized(child) if child.is_named() else child.text()
            if value is not None:
                parts.append(value)
        return [kind, parts] if parts else [kind, node.text()]

    body = callback.field("body")
    calls = []

    def call_order(node):
        for child in children(node):
            call_order(child)
        if node.kind() == "call_expression":
            function = node.field("function")
            origin = qualified(function, aliases).replace("#", ".")
            if function is not None and function.kind() == "member_expression" and not origin:
                prop = function.field("property")
                origin = prop.text() if prop is not None else ""
            calls.append(origin.removeprefix("vitest."))

    call_order(body)
    return json.dumps([calls, normalized(body)], separators=(",", ":"), ensure_ascii=False)


def behaviour(call, source: str) -> str | None:
    lines = source.splitlines()
    start, end = call.range().start.line, call.range().end.line
    while start > 0 and lines[start - 1].lstrip().startswith("//"):
        start -= 1
    # Read real comment nodes only, so strings containing // behaviour never count.
    from ast_grep_py import SgRoot
    root = SgRoot(source, "tsx").root()
    for node in root.find_all({"rule": {"kind": "comment"}}):
        if start <= node.range().start.line <= end:
            match = re.match(r"//\s*behaviour:(.*)", node.text())
            if match:
                return match.group(1).strip()
    return None


def collect(path: str, text: str) -> Collected:
    root = parse(path, text)
    aliases = imports(root)
    constants = {}
    for statement in children(root):
        for node in walk(statement):
            if node.kind() == "variable_declarator":
                name, value = node.field("name"), node.field("value")
                if name is not None and value is not None:
                    constants[name.text()] = value
            if node.kind() in {"arrow_function", "function_expression", "function_declaration"}:
                break
    cases, behaviours = [], {}

    def visit(node, prefix=(), dynamic=False, inherited=None):
        if node.kind() == "call_expression":
            if node.field("arguments") is None:
                function = node.field("function")
                if api_name(function, aliases).split(".")[0] in {"test", "it", "describe", "suite"}:
                    raise CollectionError("tagged-template registration requires runtime collection")
            function = node.field("function")
            each = None
            if function is not None and function.kind() == "call_expression":
                factory = api_name(function.field("function"), aliases)
                if ".each" in factory:
                    name = factory
                    values = arguments(function)
                    if len(values) != 1:
                        raise CollectionError("each requires one static parameter table")
                    each = literal(values[0], constants)
                    if not isinstance(each, list):
                        raise CollectionError("each requires a static array")
                else:
                    name = ""
            else:
                name = api_name(function, aliases)
            base = name.split(".")[0]
            if base in {"test", "it", "describe", "suite"}:
                if dynamic:
                    raise CollectionError("test registration inside dynamic control flow")
                args = arguments(node)
                if not args or string(args[0]) is None:
                    raise CollectionError("test and suite titles must be static strings")
                title = string(args[0])
                callbacks = [arg for arg in args[1:] if arg.kind() in {"arrow_function", "function_expression"}]
                if not callbacks:
                    if any(flag in name.split(".") for flag in ("todo", "skip")):
                        return
                    raise CollectionError(f"{title}: test callback must be inline")
                callback = callbacks[-1]
                selector = path + "::" + " > ".join((*prefix, title))
                if base in {"test", "it"}:
                    if selector in behaviours:
                        raise CollectionError(f"duplicate test title: {selector}")
                    behaviours[selector] = behaviour(node, text)
                params = callback.field("parameters") or callback.field("parameter")
                parameter_nodes = children(params) if params is not None and params.kind() == "formal_parameters" else ([params] if params is not None else [])
                names = []
                for param in parameter_nodes:
                    pattern = param.field("pattern") or param
                    if pattern.kind() not in {"identifier", "object_pattern"}:
                        raise CollectionError("unsupported test callback parameter pattern")
                    names.append(pattern)
                rows = each if each is not None else [None]
                for index, row in enumerate(rows):
                    bindings = dict(inherited or {})
                    if each is not None:
                        values = row if isinstance(row, list) else [row]
                        for param, value in zip(names, values):
                            if param.kind() == "object_pattern":
                                if not isinstance(value, dict):
                                    raise CollectionError("object parameter requires an object row")
                                for child in children(param):
                                    if child.kind() != "shorthand_property_identifier_pattern":
                                        raise CollectionError("unsupported object parameter destructuring")
                                    bindings[child.text()] = value.get(child.text())
                            else:
                                bindings[param.text()] = value
                    suffix = f"[{index}]" if each is not None else ""
                    if base in {"describe", "suite"}:
                        visit(callback.field("body"), (*prefix, title + suffix), inherited=bindings)
                    else:
                        cases.append(Case(selector + suffix, fingerprint(callback, aliases, bindings)))
                return
        for child in children(node):
            visit(child, prefix, dynamic or node.kind() in {"if_statement", "for_statement", "for_in_statement", "while_statement", "switch_statement", "function_declaration", "arrow_function", "function_expression"}, inherited)

    visit(root)
    return Collected(tuple(cases), behaviours)
