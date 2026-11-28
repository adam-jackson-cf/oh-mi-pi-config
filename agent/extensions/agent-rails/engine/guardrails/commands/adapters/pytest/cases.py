from __future__ import annotations

import itertools
import json
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from guardrails.commands.adapters.pytest.parametrize import (
    Dimension,
    Scope,
    Static,
    dimensions,
)
from guardrails.commands.adapters.pytest.syntax import (
    IMPORT_KINDS,
    Form,
    Forms,
    Role,
    import_aliases,
    walk,
)
from guardrails.foundation.source.python import defined, text_literal
from guardrails.foundation.source.tree import named, parse

if TYPE_CHECKING:
    from ast_grep_py import SgNode

SUFFIXES = (".py",)
UNITTEST_BASES = frozenset({"unittest.TestCase", "unittest.IsolatedAsyncioTestCase"})
OPAQUE_KINDS = frozenset({
    "function_definition",
    "class_definition",
    "decorated_definition",
    "expression_statement",
})
FLAT_KINDS = frozenset({
    "argument_list",
    "block",
    "parenthesized_expression",
    "else_clause",
    "finally_clause",
    "with_clause",
    "as_pattern",
    "as_pattern_target",
    "pair",
})
ASSIGNMENT_KINDS = frozenset({"assignment", "augmented_assignment"})


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


def scope_aliases(start: SgNode) -> dict[str, str]:
    names: dict[str, str] = {}
    pending = deque(named(start))
    while pending:
        node = pending.popleft()
        if node.kind() in IMPORT_KINDS:
            names.update((local, target) for local, target, _ in import_aliases(node))
        elif node.kind() not in OPAQUE_KINDS:
            pending.extend(named(node))
    return names


def assigned(statement: SgNode) -> tuple[str, SgNode] | None:
    inner = named(statement) if statement.kind() == "expression_statement" else []
    assignment = inner[0] if len(inner) == 1 else None
    if assignment is None or assignment.kind() != "assignment":
        return None
    target, value = assignment.field("left"), assignment.field("right")
    if (
        target is None
        or value is None
        or target.kind() != "identifier"
        or value.kind() == "assignment"
        or assignment.field("type") is not None
    ):
        return None
    return target.text(), value


def module_constants(root: SgNode) -> dict[str, Static]:
    constants: dict[str, Static] = {}
    for statement in named(root):
        found = assigned(statement)
        if found is not None:
            constants[found[0]] = Static(found[1], Scope({}, dict(constants)))
    return constants


def dotted_name(node: SgNode | None) -> str:
    if node is None:
        return ""
    if node.kind() == "identifier":
        return node.text()
    attribute = node.field("attribute")
    if node.kind() != "attribute" or attribute is None:
        return ""
    return f"{dotted_name(node.field('object'))}.{attribute.text()}"


def is_test_class(
    node: SgNode, aliases: Mapping[str, str], test_classes: set[str]
) -> bool:
    name = node.field("name")
    if name is not None and name.text().startswith("Test"):
        return True
    superclasses = node.field("superclasses")
    for base in named(superclasses) if superclasses is not None else []:
        if base.kind() in {"keyword_argument", "dictionary_splat"}:
            continue
        written = dotted_name(base)
        head, dot, rest = written.partition(".")
        if f"{aliases.get(head, head)}{dot}{rest}" in UNITTEST_BASES:
            return True
        if written in test_classes:
            return True
    return False


def ignored(statement: SgNode) -> bool:
    if statement.kind() in IMPORT_KINDS:
        return True
    inner = named(statement)
    return (
        statement.kind() == "expression_statement"
        and len(inner) == 1
        and text_literal(inner[0])
    )


def spliced(parent: SgNode, child: SgNode) -> bool:
    if child.kind() in FLAT_KINDS:
        return True
    if child.kind() == "expression_statement":
        inner = named(child)
        return len(inner) == 1 and inner[0].kind() in ASSIGNMENT_KINDS
    if child.kind() == "string":
        return parent.kind() == "concatenated_string"
    if child.kind() != "boolean_operator" or parent.kind() != "boolean_operator":
        return False
    operators = [node.field("operator") for node in (parent, child)]
    texts = {operator.text() for operator in operators if operator is not None}
    return len(texts) == 1


def evaluation_order(node: SgNode) -> list[SgNode]:
    children = named(node)
    if node.kind() == "conditional_expression" and len(children) == 3:
        children = [children[1], children[0], children[2]]
    ordered = []
    for item in children:
        if spliced(node, item):
            ordered.extend(evaluation_order(item))
        else:
            ordered.append(item)
    return ordered


def calls_in(statement: SgNode) -> list[SgNode]:
    found = []
    pending = deque([statement])
    while pending:
        node = pending.popleft()
        if node.kind() == "call":
            found.append(node)
        pending.extend(evaluation_order(node))
    return found


class Normalizer(Forms):
    def __init__(
        self,
        aliases: Mapping[str, str],
        bindings: dict[str, Form],
        local: Mapping[str, str],
    ) -> None:
        self.aliases = aliases
        self.bindings = bindings
        self.local = local

    def variable(self, text: str, role: Role) -> Form:
        if role is Role.LOAD and text in self.bindings:
            return self.bindings[text]
        return ("name", self.local.get(text) or self.aliases.get(text, text))

    def renamed(self, text: str) -> str:
        return self.local.get(text, text)


class TestBody:
    def __init__(
        self, function: SgNode, outer: SgNode, aliases: Mapping[str, str]
    ) -> None:
        self.function = function
        self.aliases = {**aliases, **scope_aliases(function)}
        own = function.field("name")
        parameters = function.field("parameters")
        self.arguments = [
            node.text()
            for node, role in (walk(parameters) if parameters is not None else [])
            if node.kind() == "identifier" and role is Role.ARGUMENT
        ]
        identifiers = [
            (node, role) for node, role in walk(outer) if node.kind() == "identifier"
        ]
        self.counts = Counter((node.text(), role) for node, role in identifiers)
        self.order = [
            (node.text(), "local" if role is Role.STORE else "function")
            for node, role in identifiers
            if role is Role.STORE or (role is Role.FUNCTION and node != own)
        ]

    def local_names(self, bindings: Mapping[str, Form]) -> dict[str, str]:
        names: dict[str, str] = {}
        for argument in self.arguments:
            if argument not in bindings:
                names[argument] = f"argument{len(names)}"
        for text, label in self.order:
            names.setdefault(text, f"{label}{len(names)}")
        return names

    def temporary(self, statement: SgNode) -> tuple[str, SgNode] | None:
        found = assigned(statement)
        if found is None:
            return None
        name = found[0]
        once = self.counts[name, Role.LOAD] == 1 and self.counts[name, Role.STORE] == 1
        return found if once else None

    def fingerprint(self, bindings: Mapping[str, Form]) -> str:
        normalizer = Normalizer(
            self.aliases, dict(bindings), self.local_names(bindings)
        )
        calls: list[Form] = []
        body: list[Form] = []
        block = self.function.field("body")
        for statement in named(block) if block is not None else []:
            if ignored(statement):
                continue
            calls.extend(normalizer.form(call) for call in calls_in(statement))
            temporary = self.temporary(statement)
            if temporary is None:
                body.append(normalizer.form(statement))
            else:
                normalizer.bindings[temporary[0]] = normalizer.form(temporary[1])
        return json.dumps([calls, body], separators=(",", ":"), ensure_ascii=False)


def expanded_cases(
    selector: str, body: TestBody, dimensions: Sequence[Dimension]
) -> list[Case]:
    if not dimensions:
        return [Case(selector, body.fingerprint({}))]
    cases = []
    for combination in itertools.product(*dimensions):
        labels = [label for label, _ in combination]
        bindings: dict[str, Form] = {}
        for _, row in combination:
            bindings.update(row)
        cases.append(
            Case(f"{selector}[{'-'.join(labels)}]", body.fingerprint(bindings))
        )
    return cases


def decorators(statement: SgNode) -> list[SgNode]:
    if statement.kind() != "decorated_definition":
        return []
    return [
        named(item)[0]
        for item in named(statement)
        if item.kind() == "decorator" and named(item)
    ]


class Collector:
    def __init__(self, path: str, root: SgNode) -> None:
        self.path = path
        self.aliases = scope_aliases(root)
        self.scope = Scope(self.aliases, module_constants(root))
        self.cases: list[Case] = []
        self.behaviours: dict[str, str | None] = {}
        self.test_classes: set[str] = set()

    def visit(
        self, statements: Sequence[SgNode], prefix: str, inherited: list[Dimension]
    ) -> None:
        for statement in statements:
            node = defined(statement)
            name = node.field("name")
            body = node.field("body")
            if name is None or body is None:
                continue
            if node.kind() == "class_definition" and is_test_class(
                node, self.aliases, self.test_classes
            ):
                self.test_classes.add(name.text())
                self.visit(
                    named(body),
                    f"{prefix}{name.text()}::",
                    dimensions(decorators(statement), self.scope) + inherited,
                )
            elif node.kind() == "function_definition" and name.text().startswith(
                "test"
            ):
                selector = f"{self.path}::{prefix}{name.text()}"
                self.behaviours[selector] = (
                    name.text().removeprefix("test_").replace("_", " ")
                )
                found = dimensions(decorators(statement), self.scope) + inherited
                self.cases.extend(
                    expanded_cases(
                        selector, TestBody(node, statement, self.aliases), found
                    )
                )


def collect(path: str, text: str) -> Collected:
    try:
        root = parse(path, text)
        collector = Collector(path, root)
        collector.visit(named(root), "", [])
    except (SyntaxError, ValueError, TypeError) as error:
        raise CollectionError(" ".join(str(error).split())) from error
    return Collected(tuple(collector.cases), collector.behaviours)
