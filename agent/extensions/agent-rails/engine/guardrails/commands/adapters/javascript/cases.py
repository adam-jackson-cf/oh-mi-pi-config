from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Any

from guardrails.foundation import conventions
from guardrails.foundation.source import tree, typescript

if TYPE_CHECKING:
    from ast_grep_py import SgNode

SUFFIXES = tree.languages()["typescript"]
PATTERNS_FILE = "javascript-patterns.json"
MEMBER_KINDS = frozenset({"member_expression", "subscript_expression"})
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
    pass


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


@dataclass(frozen=True)
class Imported:
    """Where a name comes from: an import specifier, the export it names (None for the
    whole module) and the members read from it."""

    module: str
    name: str | None
    members: tuple[str, ...] = ()

    def member(self, name: str) -> Imported:
        return Imported(self.module, self.name, (*self.members, name))

    def path(self) -> tuple[str, ...]:
        return self.members if self.name is None else (self.name, *self.members)

    def symbol(self) -> str | None:
        """The module export this reads: the imported name or the first member."""
        path = self.path()
        return path[0] if path else None

    def dotted(self) -> str:
        return ".".join((self.module, *self.path()))

    def label(self) -> str:
        """How findings name it: module, #export when one is named, then .members."""
        named = "" if self.name is None else f"#{self.name}"
        return self.module + named + "".join(f".{item}" for item in self.members)


def parse(path: str, text: str) -> SgNode:
    try:
        return tree.parse(path, text)
    except tree.SourceError as error:
        raise CollectionError(str(error)) from error


@cache
def vocabulary() -> dict[str, Any]:
    """javascript-patterns.json: the source patterns and the runner vocabulary."""
    found: dict[str, Any] = conventions.data(PATTERNS_FILE)
    return found


def namespace_default(specifier: str) -> bool:
    """Whether a module's default export is its namespace, so a default import reads
    its members directly (fast-check)."""
    return specifier in vocabulary()["namespace_defaults"]


def binding_origin(binding: typescript.Binding) -> Imported:
    whole = binding.imported == "default" and namespace_default(binding.specifier)
    return Imported(binding.specifier, None if whole else binding.imported)


def imports(root: SgNode) -> dict[str, Imported]:
    """Each local name an import, require() or import() binds, with its origin."""
    return {
        binding.local: binding_origin(binding)
        for site in typescript.imports(root)
        for binding in site.bindings
        if binding.local is not None and binding.form != "export-from"
    }


def arguments(call: SgNode) -> list[SgNode]:
    node = call.field("arguments")
    return children(node) if node is not None else []


def member_name(node: SgNode) -> str:
    prop = node.field("property") or node.field("index")
    return typescript.literal(prop) or (prop.text() if prop is not None else "")


def origin(node: SgNode | None, aliases: Mapping[str, Imported]) -> Imported | None:
    """The import an identifier or member chain reads, or None for a local name."""
    if node is None:
        return None
    if node.kind() == "identifier":
        return aliases.get(node.text())
    if node.kind() in MEMBER_KINDS:
        head = origin(node.field("object"), aliases)
        return None if head is None else head.member(member_name(node))
    return None


def qualified(node: SgNode | None, aliases: Mapping[str, Imported]) -> str:
    """An identifier or member chain as dotted text, imports spelled from their module."""
    if node is None:
        return ""
    if node.kind() == "identifier":
        found = aliases.get(node.text())
        return node.text() if found is None else found.dotted()
    if node.kind() in MEMBER_KINDS:
        return f"{qualified(node.field('object'), aliases)}.{member_name(node)}"
    return ""


def runner_modules() -> tuple[str, ...]:
    """The modules the test runners' APIs import from (vitest, bun:test)."""
    return tuple(runner["module"] for runner in vocabulary()["runners"].values())


def runner_api(name: str) -> str:
    for module in runner_modules():
        if name.startswith(f"{module}."):
            return name[len(module) + 1 :]
    return name


def api_name(node: SgNode | None, aliases: Mapping[str, Imported]) -> str:
    """The test-runner API a callee names: runner imports and globals by their dotted
    path, any other import by its label."""
    found = origin(node, aliases)
    if found is None:
        return qualified(node, aliases)
    return ".".join(found.path()) if found.module in runner_modules() else found.label()


def static_value(
    node: SgNode, constants: Mapping[str, SgNode], active: frozenset[str] = frozenset()
) -> Any:
    if node.kind() == "identifier" and node.text() in constants:
        if node.text() in active:
            raise CollectionError("cyclic static parameter constant")
        return static_value(constants[node.text()], constants, active | {node.text()})
    value = typescript.literal(node)
    if value is not None:
        return value
    if node.kind() in LITERAL_KINDS:
        try:
            return json.loads(node.text())
        except ValueError:
            pass
    if node.kind() == "array":
        return [static_value(item, constants, active) for item in children(node)]
    if node.kind() == "object":
        result: dict[str, Any] = {}
        for pair in children(node):
            key, entry = pair.field("key"), pair.field("value")
            if key is None or entry is None:
                raise CollectionError(
                    "parameter objects require static key/value pairs"
                )
            result[typescript.literal(key) or key.text()] = static_value(
                entry, constants, active
            )
        return result
    raise CollectionError(f"dynamic parameter value: {node.text()[:80]}")


def _local_names(callback: SgNode) -> dict[str, str]:
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
    value = typescript.literal(node)
    if value is not None:
        return ["literal", value]
    if node.kind() in LITERAL_KINDS:
        try:
            return ["literal", json.loads(node.text())]
        except ValueError:
            return None
    return None


class _Normalizer:
    def __init__(
        self,
        aliases: Mapping[str, Imported],
        bindings: Mapping[str, Any],
        callback: SgNode,
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
        if origin(node.field("object"), self.aliases) is None:
            return None
        return ["identifier", runner_api(qualified(node, self.aliases))]

    def _identifier(self, node: SgNode) -> Any:
        name = node.text()
        if name in self.bindings:
            return ["literal", self.bindings[name]]
        if name in self.temporaries:
            value = self.temporaries.pop(name)
            result = self.node(value)
            self.temporaries[name] = value
            return result
        found = runner_api(qualified(node, self.aliases))
        return ["identifier", self.local.get(name, found)]


def _call_order(
    node: SgNode, aliases: Mapping[str, Imported], calls: list[str]
) -> None:
    for child in children(node):
        _call_order(child, aliases, calls)
    if node.kind() == "call_expression":
        function = node.field("function")
        callee = qualified(function, aliases)
        if (
            function is not None
            and function.kind() == "member_expression"
            and not callee
        ):
            prop = function.field("property")
            callee = prop.text() if prop is not None else ""
        calls.append(runner_api(callee))


def _body(callback: SgNode) -> SgNode:
    body = callback.field("body")
    if body is None:
        raise CollectionError("test callback has no body")
    return body


def fingerprint(
    callback: SgNode, aliases: Mapping[str, Imported], bindings: Mapping[str, Any]
) -> str:
    body = _body(callback)
    calls: list[str] = []
    _call_order(body, aliases, calls)
    normalized = _Normalizer(aliases, bindings, callback).node(body)
    return json.dumps([calls, normalized], separators=(",", ":"), ensure_ascii=False)


def _constants(root: SgNode) -> dict[str, SgNode]:
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
        if function is None or function.kind() != "call_expression":
            return api_name(function, self.aliases), None
        factory = api_name(function.field("function"), self.aliases)
        if ".each" not in factory:
            return "", None
        values = arguments(function)
        if len(values) != 1:
            raise CollectionError("each requires one static parameter table")
        each = static_value(values[0], self.constants)
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
        title = typescript.literal(args[0]) if args else None
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
