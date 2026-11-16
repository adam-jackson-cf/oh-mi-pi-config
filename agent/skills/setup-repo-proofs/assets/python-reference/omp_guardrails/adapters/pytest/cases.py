"""Static pytest test cases with behaviour fingerprints, for TEST.growth.justified and
TEST.duplicate-path (shared checker ``omp_guardrails.diffs.growth``).

The adapter reads test source without importing or running it. ``collect(path, text)`` returns
each case's pytest node ID (``path::Class::test[row-id]``) with its fingerprint, and each test
function's inline behaviour comment. Static ``pytest.mark.parametrize`` rows on functions and
``Test*`` classes expand into cases with pytest's own row IDs.

A behaviour comment is a ``# behaviour: <text>`` comment inside the test function, between its
first decorator and its last line, or in the comment block directly above it. One comment
covers the function and all its parameter rows; the first one found is the function's.

A fingerprint is the test body after normalisation: imports resolved to qualified names,
arguments and locals renamed by position, single-use temporaries inlined, docstrings and
imports dropped, and parameter rows substituted. Two tests with the same calls in the same
order and the same assertions share a fingerprint whatever their names. A test whose rows
cannot be read statically raises ``CollectionError``.

Standard library only: it needs no ``pytest`` extra.
"""

from __future__ import annotations

import ast
import copy
import io
import itertools
import re
import tokenize
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias

TestFunction: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef
Bindings: TypeAlias = dict[str, ast.expr]
BEHAVIOUR = re.compile(r"#\s*behaviour:(.*)")
ParameterRow: TypeAlias = tuple[str, Bindings]
Dimension: TypeAlias = list[ParameterRow]


class CollectionError(ValueError):
    """The file's tests cannot be collected without running them."""


@dataclass(frozen=True)
class Case:
    """One collected test invocation with its normalised behaviour."""

    selector: str
    fingerprint: str


@dataclass(frozen=True)
class Collected:
    """A file's cases, and each test function's behaviour comment text (None when absent)."""

    cases: tuple[Case, ...]
    behaviours: Mapping[str, str | None]
def _expression(node: ast.AST) -> str:
    return ast.dump(node, include_attributes=False)


def _qualified(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_qualified(node.value)}.{node.attr}"
    return ""


def _imports(tree: ast.AST) -> dict[str, str]:
    names: dict[str, str] = {}
    pending = list(ast.iter_child_nodes(tree))
    while pending:
        node = pending.pop(0)
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            pending.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (
                    alias.name if alias.asname else alias.name.split(".")[0]
                )
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                module = f"{'.' * node.level}{node.module or ''}"
                names[alias.asname or alias.name] = (
                    f"{module}{'.' if node.module else ''}{alias.name}"
                )
    return names


def _literal_id(node: ast.expr, name: str, index: int) -> str:
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError):
        return f"{name}{index}"
    if value is None or isinstance(value, (str, int, float, bool, complex)):
        return str(value).encode("unicode_escape").decode("ascii")
    return f"{name}{index}"


def _parameter_names(node: ast.expr) -> list[str]:
    raw_names = ast.literal_eval(node)
    if isinstance(raw_names, str):
        return [part.strip() for part in raw_names.split(",")]
    if isinstance(raw_names, (tuple, list)) and all(
        isinstance(name, str) for name in raw_names
    ):
        return list(raw_names)
    raise ValueError("parametrize names must be static strings")


def _row_values(row: ast.expr, width: int) -> tuple[list[ast.expr], ast.expr | None]:
    explicit = None
    if isinstance(row, ast.Call) and _qualified(row.func).endswith(".param"):
        values = row.args
        explicit = next(
            (keyword.value for keyword in row.keywords if keyword.arg == "id"), None
        )
    else:
        values = (
            row.elts if width > 1 and isinstance(row, (ast.Tuple, ast.List)) else [row]
        )
    if len(values) != width:
        raise ValueError("parametrize row width does not match names")
    return values, explicit


def _unique_ids(rows: Dimension) -> Dimension:
    counts: dict[str, int] = {}
    for label, _ in rows:
        counts[label] = counts.get(label, 0) + 1
    offsets: dict[str, int] = {}
    unique = []
    occupied = set(counts)
    for label, bindings in rows:
        if counts[label] == 1:
            unique.append((label, bindings))
            continue
        offset = offsets.get(label, 0)
        separator = "_" if label[-1:].isdigit() else ""
        candidate = f"{label}{separator}{offset}"
        while candidate in occupied:
            offset += 1
            candidate = f"{label}{separator}{offset}"
        offsets[label] = offset + 1
        occupied.add(candidate)
        unique.append((candidate, bindings))
    return unique


def _row_id(
    names: Sequence[str],
    values: Sequence[ast.expr],
    index: int,
    explicit: ast.expr | None,
) -> str:
    label = ast.literal_eval(explicit) if explicit is not None else None
    if label is not None:
        return str(label).encode("unicode_escape").decode("ascii")
    return "-".join(
        _literal_id(value, name, index)
        for name, value in zip(names, values, strict=True)
    )


def _parameter_rows(decorator: ast.Call) -> Dimension:
    if len(decorator.args) < 2:
        raise ValueError("parametrize requires static names and rows")
    names = _parameter_names(decorator.args[0])
    rows = decorator.args[1]
    if not isinstance(rows, (ast.List, ast.Tuple)):
        raise ValueError("parametrize rows must be a static list or tuple")
    ids = next(
        (keyword.value for keyword in decorator.keywords if keyword.arg == "ids"), None
    )
    if ids is not None and not isinstance(ids, (ast.List, ast.Tuple)):
        raise ValueError("parametrize ids must be a static list or tuple")
    if isinstance(ids, (ast.List, ast.Tuple)) and len(ids.elts) != len(rows.elts):
        raise ValueError("parametrize ids must match the number of rows")
    result: Dimension = []
    for index, row in enumerate(rows.elts):
        values, explicit = _row_values(row, len(names))
        if explicit is None and isinstance(ids, (ast.List, ast.Tuple)):
            explicit = ids.elts[index]
        result.append(
            (
                _row_id(names, values, index, explicit),
                dict(zip(names, values, strict=True)),
            )
        )
    return _unique_ids(result)


class Normalizer(ast.NodeTransformer):
    """Resolve aliases and local bindings without evaluating test code."""

    def __init__(
        self, aliases: Mapping[str, str], bindings: Mapping[str, ast.expr]
    ) -> None:
        self.aliases = aliases
        self.bindings = dict(bindings)
        self.locals: dict[str, str] = {}

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if isinstance(node.ctx, ast.Load) and node.id in self.bindings:
            return self.bindings[node.id]
        if node.id in self.locals:
            return ast.Name(id=self.locals[node.id], ctx=node.ctx)
        if node.id in self.aliases:
            return ast.Name(id=self.aliases[node.id], ctx=node.ctx)
        return node

    def visit_arg(self, node: ast.arg) -> ast.AST:
        return ast.arg(arg=self.locals.get(node.arg, node.arg))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
        node.name = self.locals.get(node.name, node.name)
        return self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
        node.name = self.locals.get(node.name, node.name)
        return self.generic_visit(node)


def _local_names(
    function: TestFunction, bindings: Mapping[str, ast.expr]
) -> dict[str, str]:
    names: dict[str, str] = {}
    arguments = [
        *function.args.posonlyargs,
        *function.args.args,
        *function.args.kwonlyargs,
    ]
    arguments.extend(
        argument
        for argument in (function.args.vararg, function.args.kwarg)
        if argument is not None
    )
    for argument in arguments:
        if argument.arg not in bindings:
            names[argument.arg] = f"argument{len(names)}"
    for node in ast.walk(function):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.setdefault(node.id, f"local{len(names)}")
        elif (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node is not function
        ):
            names.setdefault(node.name, f"function{len(names)}")
    return names


def _name_count(
    function: TestFunction, target: str, context: type[ast.expr_context]
) -> int:
    return sum(
        isinstance(node, ast.Name)
        and isinstance(node.ctx, context)
        and node.id == target
        for node in ast.walk(function)
    )


def _inline_temporary(
    function: TestFunction, statement: ast.stmt, normalizer: Normalizer
) -> bool:
    if (
        not isinstance(statement, ast.Assign)
        or len(statement.targets) != 1
        or not isinstance(statement.targets[0], ast.Name)
    ):
        return False
    target = statement.targets[0].id
    if (
        _name_count(function, target, ast.Load) != 1
        or _name_count(function, target, ast.Store) != 1
    ):
        return False
    normalized = normalizer.visit(copy.deepcopy(statement.value))
    if not isinstance(normalized, ast.expr):
        return False
    normalizer.bindings[target] = normalized
    return True


def _ignored_statement(statement: ast.stmt) -> bool:
    return isinstance(statement, (ast.Import, ast.ImportFrom)) or (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )


def _fingerprint(
    function: TestFunction, aliases: Mapping[str, str], bindings: Mapping[str, ast.expr]
) -> str:
    normalizer = Normalizer({**aliases, **_imports(function)}, bindings)
    normalizer.locals = _local_names(function, bindings)
    body: list[str] = []
    calls: list[str] = []
    for statement in function.body:
        if _ignored_statement(statement):
            continue
        calls.extend(
            _expression(normalizer.visit(copy.deepcopy(node)))
            for node in ast.walk(statement)
            if isinstance(node, ast.Call)
        )
        # Calls retain execution order even when a single-use temporary is inlined.
        if not _inline_temporary(function, statement, normalizer):
            body.append(_expression(normalizer.visit(copy.deepcopy(statement))))
    return "|".join(calls) + "::" + "|".join(body)


class Constants(ast.NodeTransformer):
    """Resolve static module constants used in pytest row IDs."""

    def __init__(self) -> None:
        self.values: dict[str, ast.expr] = {}

    def visit_Name(self, node: ast.Name) -> ast.AST:
        return self.values.get(node.id, node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> ast.AST:
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                resolved = self.visit(value.value)
                if not isinstance(resolved, ast.Constant):
                    return node
                parts.append(str(resolved.value))
            else:
                return node
        return ast.Constant(value="".join(parts))

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and not node.keywords:
            if node.func.id == "len" and len(node.args) == 1:
                argument = node.args[0]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, (str, bytes)):
                    return ast.Constant(value=len(argument.value))
                if isinstance(argument, (ast.List, ast.Tuple)):
                    return ast.Constant(value=len(argument.elts))
            if (
                node.func.id == "range"
                and 1 <= len(node.args) <= 3
                and all(
                    isinstance(argument, ast.Constant) and type(argument.value) is int
                    for argument in node.args
                )
            ):
                values = [
                    argument.value
                    for argument in node.args
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, int)
                ]
                try:
                    resolved = range(*values)
                except ValueError:
                    return node
                return ast.List(
                    elts=[ast.Constant(value=value) for value in resolved],
                    ctx=ast.Load(),
                )
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "replace"
            and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)
            and len(node.args) == 2
            and not node.keywords
        ):
            old, new = node.args
            if (
                isinstance(old, ast.Constant)
                and isinstance(old.value, str)
                and isinstance(new, ast.Constant)
                and isinstance(new.value, str)
            ):
                return ast.Constant(
                    value=node.func.value.value.replace(old.value, new.value)
                )
        return node


def _dimensions(
    decorators: Sequence[ast.expr], constants: Constants, aliases: Mapping[str, str]
) -> list[Dimension]:
    dimensions = []
    for decorator in reversed(decorators):
        resolved = Normalizer(aliases, {}).visit(copy.deepcopy(decorator))
        resolved = constants.visit(resolved)
        if (
            isinstance(resolved, ast.Call)
            and _qualified(resolved.func) == "pytest.mark.parametrize"
        ):
            dimensions.append(_parameter_rows(resolved))
    return dimensions


def _expanded_cases(
    selector: str,
    function: TestFunction,
    aliases: Mapping[str, str],
    dimensions: Sequence[Dimension],
) -> list[Case]:
    if not dimensions:
        return [Case(selector, _fingerprint(function, aliases, {}))]
    cases = []
    for combination in itertools.product(*dimensions):
        labels = [label for label, _ in combination]
        bindings: Bindings = {}
        for _, row in combination:
            bindings.update(row)
        cases.append(
            Case(
                f"{selector}[{'-'.join(labels)}]",
                _fingerprint(function, aliases, bindings),
            )
        )
    return cases


def _comments(text: str) -> tuple[dict[int, str], set[int]]:
    """``{line: comment}`` for every comment, and the lines holding only a comment."""
    comments: dict[int, str] = {}
    standalone: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.COMMENT:
            line = token.start[0]
            comments[line] = token.string
            if token.line.lstrip().startswith("#"):
                standalone.add(line)
    return comments, standalone


def _behaviour(
    function: TestFunction, comments: Mapping[int, str], standalone: set[int]
) -> str | None:
    """The function's first ``# behaviour:`` text, from the comment block above to its end."""
    start = min([function.lineno, *(node.lineno for node in function.decorator_list)])
    while start - 1 in standalone:
        start -= 1
    for line in range(start, (function.end_lineno or function.lineno) + 1):
        match = BEHAVIOUR.match(comments.get(line, ""))
        if match:
            return match.group(1).strip()
    return None


def _cases(path: str, text: str) -> tuple[list[Case], dict[str, str | None]]:
    tree = ast.parse(text, filename=path)
    aliases = _imports(tree)
    constants = Constants()
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            value = constants.visit(statement.value)
            if isinstance(value, ast.expr):
                constants.values[statement.targets[0].id] = value
    comments, standalone = _comments(text)
    cases: list[Case] = []
    functions: dict[str, str | None] = {}

    def collect(
        nodes: Sequence[ast.stmt], prefix: str, inherited: list[Dimension]
    ) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                collect(
                    node.body,
                    f"{prefix}{node.name}::",
                    _dimensions(node.decorator_list, constants, aliases) + inherited,
                )
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name.startswith("test"):
                selector = f"{path}::{prefix}{node.name}"
                functions[selector] = _behaviour(node, comments, standalone)
                dimensions = (
                    _dimensions(node.decorator_list, constants, aliases) + inherited
                )
                cases.extend(_expanded_cases(selector, node, aliases, dimensions))

    collect(tree.body, "", [])
    return cases, functions


def collect(path: str, text: str) -> Collected:
    """Statically collect ``text`` (the file at ``path``); raise CollectionError when it cannot."""
    try:
        cases, behaviours = _cases(path, text)
    except (SyntaxError, ValueError, TypeError, tokenize.TokenError) as error:
        raise CollectionError(" ".join(str(error).split())) from error
    return Collected(tuple(cases), behaviours)
