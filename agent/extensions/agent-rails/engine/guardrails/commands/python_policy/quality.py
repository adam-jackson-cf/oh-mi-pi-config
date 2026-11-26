from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from guardrails.checker.python_rules.path_names import (
    skill_directory_findings,
    structure_findings,
)
from guardrails.checker.python_rules.settings import (
    PythonSettings,
    finding,
    in_roots,
    inventory_finding,
    is_excluded,
    load_settings,
    python_files,
    python_source,
    read_source,
)
from guardrails.commands.python_policy.call_names import (
    PROCESS_CALLS,
    import_names,
    qualified_name,
)
from guardrails.context import scope as event_scope
from guardrails.context.config import house_style
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    InventoryError,
    as_list,
    matches_any,
    path_prefixes,
)
from guardrails.foundation.source.python import (
    DYNAMIC_IMPORT_CALLS,
    cpython_expression,
)

CLASS_LIMIT_DEFAULTS = {
    "max_lines": 700,
    "max_public_methods": None,
    "max_init_args": 8,
    "max_instance_attributes": 12,
}
CALLABLE_NAMES = {"typing.Callable", "collections.abc.Callable", "Callable"}
ANY_NAMES = {"typing.Any", "Any"}
TYPE_ALIAS = getattr(ast, "TypeAlias", ())


def class_limits(options: PythonSettings) -> dict[str, Any]:
    return {
        key: options.number("class_limits", key, value)
        for key, value in CLASS_LIMIT_DEFAULTS.items()
    }


def init_arg_count(method: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = [*method.args.posonlyargs, *method.args.args]
    if args and args[0].arg == "self":
        args = args[1:]
    return len(args) + len(method.args.kwonlyargs)


def self_attributes(class_node: ast.ClassDef) -> set[str]:
    return {
        node.attr
        for node in ast.walk(class_node)
        if isinstance(node, ast.Attribute)
        and isinstance(node.ctx, ast.Store)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }


def methods(class_node: ast.ClassDef) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def class_shape_findings(
    node: ast.ClassDef, relpath: str, limits: dict[str, Any]
) -> list[Finding]:
    measures = [
        (
            "PYQ200",
            "max_lines",
            (node.end_lineno or node.lineno) - node.lineno + 1,
            "lines",
            "split the class by responsibility",
            node.lineno,
        ),
        (
            "PYQ201",
            "max_public_methods",
            sum(1 for method in methods(node) if not method.name.startswith("_")),
            "public methods",
            "split the interface into smaller collaborators",
            node.lineno,
        ),
        (
            "PYQ203",
            "max_instance_attributes",
            len(self_attributes(node)),
            "instance attributes",
            "group related state into value objects",
            node.lineno,
        ),
    ]
    for method in methods(node):
        if method.name == "__init__":
            measures.append((
                "PYQ202",
                "max_init_args",
                init_arg_count(method),
                "constructor parameters",
                "pass a parameter object or split the class",
                method.lineno,
            ))
    return [
        finding(
            rule,
            relpath,
            line,
            f"class '{node.name}' has {count} {label} > {limits[key]}",
            fix,
        )
        for rule, key, count, label, fix, line in measures
        if limits[key] is not None and count > limits[key]
    ]


def base_names(class_node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in class_node.bases:
        target = base.value if isinstance(base, ast.Subscript) else base
        dotted = ast.unparse(target)
        names.update({dotted, dotted.rsplit(".", 1)[-1]})
    return names


def analyzer_findings(
    node: ast.ClassDef, relpath: str, options: PythonSettings
) -> list[Finding]:
    found: list[Finding] = []
    bases = base_names(node)
    approved = set(as_list(options.raw.get("analyzer_bases")))
    if (
        in_roots(relpath, options.paths("analyzer_roots"))
        and node.name.endswith("Analyzer")
        and not approved & bases
    ):
        found.append(
            finding(
                "PYQ210",
                relpath,
                node.lineno,
                f"analyzer class '{node.name}' inherits no approved analyzer base",
                "inherit a base listed in checkers.python.analyzer_bases",
            )
        )
    defined = {method.name for method in methods(node)}
    for base, entrypoint in options.mapping("analyzer_entrypoints").items():
        if base in bases and entrypoint not in defined:
            found.append(
                finding(
                    "PYQ211",
                    relpath,
                    node.lineno,
                    f"project analyzer class '{node.name}' does not define {entrypoint}",
                    f"implement {entrypoint} on the class",
                )
            )
    return found


def seam_findings(
    tree: ast.Module, relpath: str, options: PythonSettings
) -> list[Finding]:
    process_scope = in_roots(relpath, options.scope("process_roots"))
    dynamic_scope = in_roots(
        relpath, options.scope("dynamic_import_roots")
    ) and relpath not in set(options.paths("dynamic_import_adapters"))
    if not process_scope and not dynamic_scope:
        return []
    allowed = set(as_list(options.mapping("subprocess_wrappers").get(relpath)))
    found: list[Finding] = []

    def visit(node: ast.AST, names: dict[str, str], owner: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = f"{owner}.{node.name}" if owner else node.name
            names = names | import_names(node)
        elif isinstance(node, ast.Lambda):
            owner = f"{owner}.<lambda>"
        if isinstance(node, ast.Call):
            name = qualified_name(node.func, names)
            if process_scope and name in PROCESS_CALLS and owner not in allowed:
                found.append(
                    finding(
                        "PYQ220",
                        relpath,
                        node.lineno,
                        f"{name} runs outside an approved process wrapper",
                        "move the call into a wrapper listed in checkers.python.subprocess_wrappers",
                    )
                )
            if dynamic_scope and name in DYNAMIC_IMPORT_CALLS:
                found.append(
                    finding(
                        "PYQ221",
                        relpath,
                        node.lineno,
                        f"{name} is a dynamic import outside an approved adapter",
                        "use a static import or list the adapter in "
                        "checkers.python.dynamic_import_adapters",
                    )
                )
        for child in ast.iter_child_nodes(node):
            visit(child, names, owner)

    visit(tree, import_names(tree), "")
    return found


def annotation_tree(annotation: ast.AST) -> ast.AST:
    if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
        return cpython_expression(annotation.value) or annotation
    return annotation


def module_aliases(tree: ast.Module) -> dict[str, ast.AST]:
    aliases: dict[str, ast.AST] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            aliases[node.targets[0].id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value
        ):
            aliases[node.target.id] = node.value
        elif isinstance(node, TYPE_ALIAS) and isinstance(node.name, ast.Name):
            aliases[node.name.id] = node.value
    return aliases


def annotation_contains(
    annotation: ast.AST,
    names: dict[str, str],
    aliases: dict[str, ast.AST],
    wanted: set[str],
    seen: frozenset[str] = frozenset(),
) -> bool:
    for part in ast.walk(annotation_tree(annotation)):
        if qualified_name(part, names) in wanted:
            return True
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            parsed = annotation_tree(part)
            if parsed is not part and annotation_contains(
                parsed, names, aliases, wanted, seen
            ):
                return True
        if (
            isinstance(part, ast.Name)
            and part.id in aliases
            and part.id not in seen
            and annotation_contains(
                aliases[part.id], names, aliases, wanted, seen | {part.id}
            )
        ):
            return True
    return False


def callable_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    return [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
        *([node.args.vararg] if node.args.vararg else []),
        *([node.args.kwarg] if node.args.kwarg else []),
    ]


def collaborator_findings(
    annotation: ast.AST,
    binding: str,
    relpath: str,
    context: tuple[dict[str, str], dict[str, ast.AST], dict[str, Any]],
) -> list[Finding]:
    names, aliases, pinned = context
    line = getattr(annotation, "lineno", 1)
    found: list[Finding] = []
    if annotation_contains(annotation, names, {}, CALLABLE_NAMES):
        found.append(
            finding(
                "PYQ230",
                relpath,
                line,
                f"collaborator '{binding}' uses an inline Callable",
                "define and use a named callable alias",
            )
        )
    is_json_record = pinned.get(binding) == ast.unparse(annotation_tree(annotation))
    if not is_json_record and annotation_contains(
        annotation, names, aliases, ANY_NAMES
    ):
        found.append(
            finding(
                "PYQ231",
                relpath,
                line,
                f"collaborator '{binding}' annotation contains Any",
                "use a concrete type or a capability protocol, or pin a JSON-record binding in "
                "checkers.python.contract_data_annotations",
            )
        )
    return found


def contract_findings(
    tree: ast.Module, relpath: str, options: PythonSettings
) -> list[Finding]:
    if relpath not in set(options.paths("contract_files")):
        return []
    pinned = options.mapping("contract_data_annotations").get(relpath)
    context = (
        import_names(tree),
        module_aliases(tree),
        pinned if isinstance(pinned, dict) else {},
    )
    found: list[Finding] = []

    def visit(node: ast.AST, owner: str) -> None:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = f"{owner}.{node.name}" if owner else node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in callable_arguments(node):
                if arg.annotation:
                    found.extend(
                        collaborator_findings(
                            arg.annotation, f"{owner}.{arg.arg}", relpath, context
                        )
                    )
            return
        if isinstance(node, ast.AnnAssign) and owner:
            binding = f"{owner}.{ast.unparse(node.target)}"
            found.extend(
                collaborator_findings(node.annotation, binding, relpath, context)
            )
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree, "")
    return found


def code_findings(repo: Path, relpath: str, options: PythonSettings) -> list[Finding]:
    source = read_source(repo, relpath)
    if source.tree is None:
        return [
            finding(
                "PYQ299", relpath, None, source.error, "restore readable UTF-8 Python"
            )
        ]
    limits = class_limits(options)
    found: list[Finding] = []
    for node in ast.walk(source.tree):
        if isinstance(node, ast.ClassDef):
            found.extend(class_shape_findings(node, relpath, limits))
            found.extend(analyzer_findings(node, relpath, options))
    found.extend(seam_findings(source.tree, relpath, options))
    found.extend(contract_findings(source.tree, relpath, options))
    return found


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = load_settings(config)
    try:
        selected = event_scope.select(repo, argv)
        content_paths = (
            argv if argv and not any(raw.startswith("-") for raw in argv) else []
        )
        existing_segments = path_prefixes(selected.existing_paths or ())
        files = [
            path
            for path in selected.inventory
            if python_source(path) and not is_excluded(path, options)
        ]
        roots = options.paths("maintained_roots")
        found: list[Finding] = []
        with event_scope.checkout(repo, selected) as snapshot:
            for relpath in files:
                if not (snapshot / relpath).is_file():
                    continue
                if matches_any(relpath, selected.paths):
                    found.extend(
                        structure_findings(relpath, options, existing_segments)
                    )
            found.extend(
                skill_directory_findings(
                    snapshot, options, selected.paths, existing_segments
                )
            )
        for relpath in python_files(repo, options, content_paths):
            if in_roots(relpath, roots):
                found.extend(code_findings(repo, relpath, options))
    except InventoryError as error:
        return [inventory_finding("PYQ100", error)]
    return house_style.apply_exceptions(found, config)
