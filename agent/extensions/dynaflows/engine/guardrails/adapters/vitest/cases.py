"""Static Vitest collection; never imports or executes the target's JavaScript.

Selectors use the shared ``path::suite::test[row]`` convention. Dynamic registration
is rejected rather than silently losing cases from growth and duplicate checks.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ast_grep_py import SgNode

REGISTRARS = frozenset({"test", "it", "describe", "suite"})
TEST_REGISTRARS = frozenset({"test", "it"})
SUITE_REGISTRARS = frozenset({"describe", "suite"})
CALLBACK_KINDS = frozenset({"arrow_function", "function_expression"})
FUNCTION_KINDS = frozenset({
    "arrow_function",
    "function_expression",
    "function_declaration",
})
DYNAMIC_KINDS = frozenset({
    "if_statement",
    "for_statement",
    "for_in_statement",
    "while_statement",
    "switch_statement",
    "function_declaration",
    "arrow_function",
    "function_expression",
})
LITERAL_KINDS = frozenset({"number", "true", "false", "null", "unary_expression"})
ELIDED_KINDS = frozenset({"comment", "type_annotation"})


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


def children(node: SgNode) -> list[SgNode]:
    return [
        item for item in node.children() if item.is_named() and item.kind() != "comment"
    ]


def walk(node: SgNode) -> Iterator[SgNode]:
    yield node
    for item in children(node):
        yield from walk(item)


def parse(path: str, text: str) -> SgNode:
    from ast_grep_py import SgRoot

    root = SgRoot(
        text, "tsx" if path.endswith((".tsx", ".jsx")) else "typescript"
    ).root()
    if any(node.kind() == "ERROR" for node in walk(root)):
        raise CollectionError(f"invalid JavaScript/TypeScript in {path}")
    return root


def string(node: SgNode | None) -> str | None:
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


def _import_bindings(node: SgNode, module: str) -> dict[str, str]:
    """The local bindings one import specifier, namespace import or default import introduces."""
    kind = node.kind()
    if kind == "import_specifier":
        name, alias = node.field("name"), node.field("alias")
        return (
            {(alias or name).text(): f"{module}#{name.text()}"}
            if name is not None
            else {}
        )
    if kind == "namespace_import":
        names = children(node)
        return {names[-1].text(): module} if names else {}
    if kind == "import_clause":
        target = module if module == "fast-check" else f"{module}#default"
        return {
            child.text(): target
            for child in children(node)
            if child.kind() == "identifier"
        }
    return {}


def imports(root: SgNode) -> dict[str, str]:
    """Local binding -> module#export, retaining namespace member resolution."""
    found: dict[str, str] = {}
    for statement in root.find_all({"rule": {"kind": "import_statement"}}):
        module = string(statement.field("source"))
        if module is None:
            continue
        for node in walk(statement):
            found.update(_import_bindings(node, module))
    return found


def arguments(call: SgNode) -> list[SgNode]:
    node = call.field("arguments")
    return children(node) if node is not None else []


def qualified(node: SgNode | None, aliases: Mapping[str, str]) -> str:
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


def api_name(node: SgNode | None, aliases: Mapping[str, str]) -> str:
    name = qualified(node, aliases)
    for prefix in ("vitest#", "vitest."):
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def literal(
    node: SgNode, constants: Mapping[str, SgNode], active: frozenset[str] = frozenset()
) -> Any:
    if node.kind() == "identifier" and node.text() in constants:
        if node.text() in active:
            raise CollectionError("cyclic static parameter constant")
        return literal(constants[node.text()], constants, active | {node.text()})
    value = string(node)
    if value is not None:
        return value
    if node.kind() in LITERAL_KINDS:
        try:
            return json.loads(node.text())
        except ValueError:
            pass
    if node.kind() == "array":
        return [literal(item, constants, active) for item in children(node)]
    if node.kind() == "object":
        result: dict[str, Any] = {}
        for pair in children(node):
            key, entry = pair.field("key"), pair.field("value")
            if key is None or entry is None:
                raise CollectionError(
                    "parameter objects require static key/value pairs"
                )
            result[string(key) or key.text()] = literal(entry, constants, active)
        return result
    raise CollectionError(f"dynamic parameter value: {node.text()[:80]}")


def _local_names(callback: SgNode) -> dict[str, str]:
    """Declared locals and parameters of a test callback, renamed by declaration order."""
    local: dict[str, str] = {}
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
    return local


def _temporary(node: SgNode, nodes: list[SgNode]) -> tuple[str, SgNode] | None:
    """``(name, value)`` when ``node`` declares a single-use, never-reassigned local."""
    if node.kind() != "variable_declarator":
        return None
    name, value = node.field("name"), node.field("value")
    parent = node.parent()
    if name is None or value is None or name.kind() != "identifier":
        return None
    if parent is None or len(children(parent)) != 1:
        return None
    occurrences = [
        item
        for item in nodes
        if item.kind() == "identifier" and item.text() == name.text()
    ]
    if len(occurrences) != 2 or any(
        item.kind() in {"assignment_expression", "update_expression"}
        and name.text() in item.text()
        for item in nodes
    ):
        return None
    return name.text(), value


def _temporaries(callback: SgNode) -> dict[str, SgNode]:
    nodes = list(walk(callback))
    return dict(
        found for node in nodes if (found := _temporary(node, nodes)) is not None
    )


def _constant(node: SgNode) -> list[Any] | None:
    value = string(node)
    if value is not None:
        return ["literal", value]
    if node.kind() in LITERAL_KINDS:
        try:
            return ["literal", json.loads(node.text())]
        except ValueError:
            return None
    return None


class _Normalizer:
    """Normalise AST structure, imported aliases, parameter values and local names."""

    def __init__(
        self, aliases: Mapping[str, str], bindings: Mapping[str, Any], callback: SgNode
    ) -> None:
        self.aliases = aliases
        self.bindings = bindings
        self.local = _local_names(callback)
        self.temporaries = _temporaries(callback)

    def node(self, node: SgNode) -> Any:
        kind = node.kind()
        if kind in ELIDED_KINDS or self._elided_declaration(node):
            return None
        if kind == "identifier":
            return self._identifier(node)
        imported = self._imported_member(node) if kind == "member_expression" else None
        if imported is not None:
            return imported
        constant = _constant(node)
        if constant is not None:
            return constant
        parts = []
        for child in node.children():
            if child.kind() in ELIDED_KINDS or child.text() == ";":
                continue
            value = self.node(child) if child.is_named() else child.text()
            if value is not None:
                parts.append(value)
        return [kind, parts] if parts else [kind, node.text()]

    def _elided_declaration(self, node: SgNode) -> bool:
        if node.kind() not in {"lexical_declaration", "variable_declaration"}:
            return False
        declarations = children(node)
        return bool(declarations) and all(
            item.kind() == "variable_declarator"
            and (name := item.field("name")) is not None
            and name.text() in self.temporaries
            for item in declarations
        )

    def _imported_member(self, node: SgNode) -> list[str] | None:
        origin = qualified(node, self.aliases)
        head = qualified(node.field("object"), self.aliases)
        if head and any(head.startswith(module) for module in self.aliases.values()):
            return ["identifier", origin.replace("#", ".").removeprefix("vitest.")]
        return None

    def _identifier(self, node: SgNode) -> Any:
        name = node.text()
        if name in self.bindings:
            return ["literal", self.bindings[name]]
        if name in self.temporaries:
            value = self.temporaries.pop(name)
            result = self.node(value)
            self.temporaries[name] = value
            return result
        origin = self.aliases.get(name, name).removeprefix("vitest#")
        return ["identifier", self.local.get(name, origin.replace("#", "."))]


def _call_order(node: SgNode, aliases: Mapping[str, str], calls: list[str]) -> None:
    for child in children(node):
        _call_order(child, aliases, calls)
    if node.kind() == "call_expression":
        function = node.field("function")
        origin = qualified(function, aliases).replace("#", ".")
        if (
            function is not None
            and function.kind() == "member_expression"
            and not origin
        ):
            prop = function.field("property")
            origin = prop.text() if prop is not None else ""
        calls.append(origin.removeprefix("vitest."))


def _body(callback: SgNode) -> SgNode:
    body = callback.field("body")
    if body is None:
        raise CollectionError("test callback has no body")
    return body


def fingerprint(
    callback: SgNode, aliases: Mapping[str, str], bindings: Mapping[str, Any]
) -> str:
    """Normalise AST structure, imported aliases, parameter values and local names."""
    body = _body(callback)
    calls: list[str] = []
    _call_order(body, aliases, calls)
    normalized = _Normalizer(aliases, bindings, callback).node(body)
    return json.dumps([calls, normalized], separators=(",", ":"), ensure_ascii=False)


def _constants(root: SgNode) -> dict[str, SgNode]:
    """Top-level declarations a parameter table may name, up to the first function."""
    constants: dict[str, SgNode] = {}
    for statement in children(root):
        for node in walk(statement):
            if node.kind() == "variable_declarator":
                name, value = node.field("name"), node.field("value")
                if name is not None and value is not None:
                    constants[name.text()] = value
            if node.kind() in FUNCTION_KINDS:
                break
    return constants


def _parameter_patterns(callback: SgNode) -> list[SgNode]:
    params = callback.field("parameters") or callback.field("parameter")
    parameter_nodes: list[SgNode]
    if params is None:
        parameter_nodes = []
    elif params.kind() == "formal_parameters":
        parameter_nodes = children(params)
    else:
        parameter_nodes = [params]
    names = []
    for param in parameter_nodes:
        pattern = param.field("pattern") or param
        if pattern.kind() not in {"identifier", "object_pattern"}:
            raise CollectionError("unsupported test callback parameter pattern")
        names.append(pattern)
    return names


def _bind_row(names: list[SgNode], row: Any, bindings: dict[str, Any]) -> None:
    values = row if isinstance(row, list) else [row]
    for param, value in zip(names, values, strict=False):
        if param.kind() != "object_pattern":
            bindings[param.text()] = value
            continue
        if not isinstance(value, dict):
            raise CollectionError("object parameter requires an object row")
        for child in children(param):
            if child.kind() != "shorthand_property_identifier_pattern":
                raise CollectionError("unsupported object parameter destructuring")
            bindings[child.text()] = value.get(child.text())


@dataclass(frozen=True)
class _Registration:
    """One static ``test``/``it``/``describe``/``suite`` call and its parameter table."""

    base: str
    selector: str
    callback: SgNode
    each: list[Any] | None


class _Collector:
    def __init__(self, path: str, root: SgNode) -> None:
        self.path = path
        self.aliases = imports(root)
        self.constants = _constants(root)
        self.cases: list[Case] = []
        self.behaviours: dict[str, str | None] = {}

    def visit(
        self,
        node: SgNode,
        prefix: tuple[str, ...] = (),
        dynamic: bool = False,
        inherited: Mapping[str, Any] | None = None,
    ) -> None:
        if node.kind() == "call_expression" and self._registered(
            node, prefix, dynamic, inherited
        ):
            return
        nested = dynamic or node.kind() in DYNAMIC_KINDS
        for child in children(node):
            self.visit(child, prefix, nested, inherited)

    def _registrar(self, function: SgNode | None) -> tuple[str, list[Any] | None]:
        """The registering API's name and, for ``.each``, its static parameter table."""
        if function is None or function.kind() != "call_expression":
            return api_name(function, self.aliases), None
        factory = api_name(function.field("function"), self.aliases)
        if ".each" not in factory:
            return "", None
        values = arguments(function)
        if len(values) != 1:
            raise CollectionError("each requires one static parameter table")
        each = literal(values[0], self.constants)
        if not isinstance(each, list):
            raise CollectionError("each requires a static array")
        return factory, each

    def _registered(
        self,
        node: SgNode,
        prefix: tuple[str, ...],
        dynamic: bool,
        inherited: Mapping[str, Any] | None,
    ) -> bool:
        """Record a static registration call; False when ``node`` registers no test or suite."""
        function = node.field("function")
        if (
            node.field("arguments") is None
            and api_name(function, self.aliases).split(".")[0] in REGISTRARS
        ):
            raise CollectionError(
                "tagged-template registration requires runtime collection"
            )
        name, each = self._registrar(function)
        base = name.split(".")[0]
        if base not in REGISTRARS:
            return False
        if dynamic:
            raise CollectionError("test registration inside dynamic control flow")
        args = arguments(node)
        title = string(args[0]) if args else None
        if title is None:
            raise CollectionError("test and suite titles must be static strings")
        callbacks = [arg for arg in args[1:] if arg.kind() in CALLBACK_KINDS]
        if not callbacks:
            if any(flag in name.split(".") for flag in ("todo", "skip")):
                return True
            raise CollectionError(f"{title}: test callback must be inline")
        selector = self.path + "::" + " > ".join((*prefix, title))
        if base in TEST_REGISTRARS:
            if selector in self.behaviours:
                raise CollectionError(f"duplicate test title: {selector}")
            self.behaviours[selector] = title
        self._expand(
            _Registration(base, selector, callbacks[-1], each),
            (*prefix, title),
            inherited,
        )
        return True

    def _expand(
        self,
        registration: _Registration,
        path: tuple[str, ...],
        inherited: Mapping[str, Any] | None,
    ) -> None:
        """Collect one case per parameter row, or visit the suite body once per row."""
        names = _parameter_patterns(registration.callback)
        each = registration.each
        for index, row in enumerate(each if each is not None else [None]):
            bindings = dict(inherited or {})
            if each is not None:
                _bind_row(names, row, bindings)
            suffix = f"[{index}]" if each is not None else ""
            if registration.base in SUITE_REGISTRARS:
                self.visit(
                    _body(registration.callback),
                    (*path[:-1], path[-1] + suffix),
                    inherited=bindings,
                )
            else:
                self.cases.append(
                    Case(
                        registration.selector + suffix,
                        fingerprint(registration.callback, self.aliases, bindings),
                    )
                )


def collect(path: str, text: str) -> Collected:
    root = parse(path, text)
    collector = _Collector(path, root)
    collector.visit(root)
    return Collected(tuple(collector.cases), collector.behaviours)
