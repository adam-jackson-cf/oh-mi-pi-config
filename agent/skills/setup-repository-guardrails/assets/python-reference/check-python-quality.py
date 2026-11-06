#!/usr/bin/env python3
"""Validate Python structure, class shape, and seam boundary rules."""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from python_quality_scope import (
    EXCLUSION_RULES,
    MAINTAINED_ROOTS,
    SKILL_ROOTS,
    discover_unexcluded_python_files,
    exclusion_for_relpath,
    is_maintained_python_relpath,
    path_matches_prefix,
    repo_relative_path,
)

CLASS_MAX_LINES = 700
CLASS_MAX_INIT_ARGS = 8
CLASS_MAX_INSTANCE_ATTRIBUTES = 12

# Per-target configuration: populate from the target inventory and approved maps.
GENERIC_NAME_ALLOWLIST: set[str] = set()
GENERIC_NAME_PARTS = {"common", "helpers", "misc", "stuff", "temp"}
ALLOWED_ROOT_FILES: set[str] = set()
ALLOWED_TEST_GROUPS: set[str] = set()
SCRIPT_NAME_ALLOWLIST: set[str] = set()
SEAM_SUBPROCESS_WRAPPERS: dict[str, set[str]] = {}
ADAPTER_IMPORT_ALLOWLIST: set[str] = set()
TEST_ROOTS: tuple[str, ...] = ()
SCRIPT_ROOTS: tuple[str, ...] = ()
NON_PACKAGE_DIRECTORY_NAMES: set[str] = set()
ANALYZER_ROOTS: tuple[str, ...] = ()
APPROVED_ANALYZER_BASES: set[str] = set()
# Map target project-analyzer bases/mixins to their required entrypoint method.
PROJECT_ANALYZER_ENTRYPOINTS: dict[str, str] = {}
DYNAMIC_IMPORT_ROOTS: tuple[str, ...] = ()
CONTRACT_FILES: set[str] = set()


@dataclass(frozen=True)
class Diagnostic:
    """One quality-gate diagnostic."""

    code: str
    relpath: str
    line: int
    message: str

    def render(self) -> str:
        return f"{self.relpath}:{self.line}: {self.code} {self.message}"


def _is_snake_case_name(name: str) -> bool:
    if name == "__init__":
        return True
    return bool(name) and all(
        char == "_" or char.islower() or char.isdigit() for char in name
    )


def _is_valid_skill_name(name: str) -> bool:
    return bool(name) and all(
        char == "-" or char.islower() or char.isdigit() for char in name
    )


def _generic_name_diagnostics(relpath: str) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    parts = Path(relpath).parts[:-1]
    for index, part in enumerate(parts):
        prefix = "/".join(parts[: index + 1])
        if part in GENERIC_NAME_PARTS and prefix not in GENERIC_NAME_ALLOWLIST:
            diagnostics.append(
                Diagnostic(
                    "PYQ103",
                    relpath,
                    1,
                    f"generic catch-all directory name '{part}' is not allowed",
                )
            )
    stem = Path(relpath).stem
    if stem in GENERIC_NAME_PARTS and relpath not in GENERIC_NAME_ALLOWLIST:
        diagnostics.append(
            Diagnostic(
                "PYQ104",
                relpath,
                1,
                f"generic catch-all module name '{stem}' is not allowed",
            )
        )
    return diagnostics


def _root_diagnostics(relpath: str) -> list[Diagnostic]:
    if any(path_matches_prefix(relpath, root) for root in MAINTAINED_ROOTS):
        return []
    return [
        Diagnostic(
            "PYQ100",
            relpath,
            1,
            "Python file is outside canonical maintained roots",
        )
    ]


def _test_group_diagnostics(relpath: str) -> list[Diagnostic]:
    root = next((root for root in TEST_ROOTS if path_matches_prefix(relpath, root)), None)
    if root is None:
        return []
    if relpath in ALLOWED_ROOT_FILES:
        return []
    path = Path(relpath)
    parts = path.relative_to(root).parts
    if len(parts) <= 1:
        return [
            Diagnostic(
                "PYQ102",
                relpath,
                1,
                "test file must live under a canonical test grouping",
            )
        ]
    group = parts[0]
    if group in ALLOWED_TEST_GROUPS:
        return []
    return [
        Diagnostic(
            "PYQ102",
            relpath,
            1,
            f"test path group '{group}' is not a canonical test grouping",
        )
    ]


def _package_dir_diagnostics(relpath: str) -> list[Diagnostic]:
    if any(path_matches_prefix(relpath, root) for root in SKILL_ROOTS):
        return []

    diagnostics: list[Diagnostic] = []
    path = Path(relpath)
    ignored_parts = NON_PACKAGE_DIRECTORY_NAMES
    for part in path.parts[:-1]:
        if part in ignored_parts:
            continue
        if not _is_snake_case_name(part):
            diagnostics.append(
                Diagnostic(
                    "PYQ105",
                    relpath,
                    1,
                    f"Python package directory '{part}' must use snake_case",
                )
            )
    return diagnostics


def _module_name_diagnostics(relpath: str) -> list[Diagnostic]:
    if any(path_matches_prefix(relpath, root) for root in SCRIPT_ROOTS):
        return []
    path = Path(relpath)
    stem = path.stem
    if stem.startswith("test-"):
        return [
            Diagnostic(
                "PYQ107",
                relpath,
                1,
                f"test module '{path.name}' must use test_snake_case",
            )
        ]
    if _is_snake_case_name(stem):
        return []
    return [
        Diagnostic(
            "PYQ107",
            relpath,
            1,
            f"Python module '{path.name}' must use snake_case",
        )
    ]


def _skill_name_diagnostics(relpath: str) -> list[Diagnostic]:
    root = next((root for root in SKILL_ROOTS if path_matches_prefix(relpath, root)), None)
    if root is None:
        return []
    path = Path(relpath)
    skill_name = path.relative_to(root).parts[0]
    if _is_valid_skill_name(skill_name):
        return []
    return [
        Diagnostic(
            "PYQ106",
            relpath,
            1,
            f"skill directory '{skill_name}' must use kebab-case",
        )
    ]


def structure_diagnostics_for_relpath(relpath: str) -> list[Diagnostic]:
    """Return deterministic structure diagnostics for a Python relpath."""
    normalized = relpath.removeprefix("./")
    exclusion = exclusion_for_relpath(normalized)
    if exclusion is not None:
        return []

    diagnostics = _root_diagnostics(normalized)
    if diagnostics:
        return diagnostics

    path = Path(normalized)
    if any(path_matches_prefix(normalized, root) for root in SCRIPT_ROOTS):
        if path.name not in SCRIPT_NAME_ALLOWLIST:
            diagnostics.append(
                Diagnostic(
                    "PYQ101",
                    normalized,
                    1,
                    f"script module '{path.name}' is not in the script name allowlist",
                )
            )
        return diagnostics + _generic_name_diagnostics(normalized)

    diagnostics.extend(_test_group_diagnostics(normalized))
    diagnostics.extend(_package_dir_diagnostics(normalized))
    diagnostics.extend(_skill_name_diagnostics(normalized))
    diagnostics.extend(_module_name_diagnostics(normalized))
    diagnostics.extend(_generic_name_diagnostics(normalized))
    return diagnostics


def structure_diagnostics(repo_root: Path, paths: Sequence[Path]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for path in paths:
        relpath = repo_relative_path(repo_root, path)
        diagnostics.extend(structure_diagnostics_for_relpath(relpath))
    return diagnostics


def _base_names(class_node: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for base in class_node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
    return names


def _init_arg_count(method: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    args = list(method.args.posonlyargs) + list(method.args.args)
    if args and args[0].arg == "self":
        args = args[1:]
    return len(args) + len(method.args.kwonlyargs)


def _self_attributes(class_node: ast.ClassDef) -> set[str]:
    attributes: set[str] = set()
    pending = list(class_node.body)
    while pending:
        node = pending.pop()
        if isinstance(node, ast.ClassDef):
            continue
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            attributes.add(node.attr)
        pending.extend(ast.iter_child_nodes(node))
    return attributes


def _function_scope_for_line(tree: ast.AST, line: int) -> str | None:
    owner: str | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        end_line = getattr(node, "end_lineno", node.lineno)
        if node.lineno <= line <= end_line:
            owner = node.name
    return owner


def _is_subprocess_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"run", "Popen"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
    )


def _is_importlib_import_module_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
    )


def _annotation_name(annotation: ast.AST | None) -> str:
    if annotation is None:
        return ""
    if isinstance(annotation, ast.Name):
        return annotation.id
    if isinstance(annotation, ast.Attribute):
        return annotation.attr
    if isinstance(annotation, ast.Subscript):
        return _annotation_name(annotation.value)
    if isinstance(annotation, ast.Constant):
        return str(annotation.value)
    return ""


def _annotation_subscript_children(node: ast.Subscript) -> tuple[ast.AST, ...]:
    annotation_name = _annotation_name(node.value)
    if annotation_name == "Literal":
        return (node.value,)
    if annotation_name == "Annotated" and isinstance(node.slice, ast.Tuple):
        return (node.value, node.slice.elts[0])
    return (node.value, node.slice)


def _annotation_names(annotation: ast.AST | None) -> set[str]:
    names: set[str] = set()
    pending = [annotation] if annotation is not None else []
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Subscript):
            pending.extend(_annotation_subscript_children(node))
        elif isinstance(node, ast.BinOp):
            pending.extend([node.left, node.right])
        elif isinstance(node, ast.Tuple | ast.List):
            pending.extend(node.elts)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                pending.append(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                names.add(node.value)
    return names


def _class_shape_diagnostics(
    class_node: ast.ClassDef, relpath: str
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    line_count = (
        (getattr(class_node, "end_lineno", class_node.lineno) or class_node.lineno)
        - class_node.lineno
        + 1
    )
    if line_count > CLASS_MAX_LINES:
        diagnostics.append(
            Diagnostic(
                "PYQ200",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' has {line_count} lines > {CLASS_MAX_LINES}",
            )
        )
    attributes = _self_attributes(class_node)
    if len(attributes) > CLASS_MAX_INSTANCE_ATTRIBUTES:
        diagnostics.append(
            Diagnostic(
                "PYQ203",
                relpath,
                class_node.lineno,
                f"class '{class_node.name}' assigns {len(attributes)} instance attributes > {CLASS_MAX_INSTANCE_ATTRIBUTES}",
            )
        )
    return diagnostics


def _constructor_diagnostics(
    class_node: ast.ClassDef, relpath: str
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for method in class_node.body:
        if not isinstance(method, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if method.name != "__init__":
            continue
        arg_count = _init_arg_count(method)
        if arg_count > CLASS_MAX_INIT_ARGS:
            diagnostics.append(
                Diagnostic(
                    "PYQ202",
                    relpath,
                    method.lineno,
                    f"constructor for '{class_node.name}' has {arg_count} parameters > {CLASS_MAX_INIT_ARGS}",
                )
            )
    return diagnostics


def _analyzer_boundary_diagnostics(
    class_node: ast.ClassDef, relpath: str
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    bases = _base_names(class_node)
    if (
        any(path_matches_prefix(relpath, root) for root in ANALYZER_ROOTS)
        and class_node.name.endswith("Analyzer")
        and not (APPROVED_ANALYZER_BASES & bases)
    ):
        diagnostics.append(
            Diagnostic(
                "PYQ210",
                relpath,
                class_node.lineno,
                f"analyzer class '{class_node.name}' must inherit an approved target base or mixin",
            )
        )
    for base, entrypoint in PROJECT_ANALYZER_ENTRYPOINTS.items():
        if base not in bases:
            continue
        defines_project_analysis = any(
            isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            and child.name == entrypoint
            for child in class_node.body
        )
        if not defines_project_analysis:
            diagnostics.append(
                Diagnostic(
                    "PYQ211",
                    relpath,
                    class_node.lineno,
                    f"project analyzer class '{class_node.name}' must define {entrypoint}",
                )
            )
    return diagnostics


def _subprocess_boundary_diagnostics(tree: ast.AST, relpath: str) -> list[Diagnostic]:
    allowed_wrappers = SEAM_SUBPROCESS_WRAPPERS.get(relpath, set())
    diagnostics: list[Diagnostic] = []
    for node in ast.walk(tree):
        if not _is_subprocess_call(node):
            continue
        owner = _function_scope_for_line(tree, node.lineno)
        if owner not in allowed_wrappers:
            diagnostics.append(
                Diagnostic(
                    "PYQ220",
                    relpath,
                    node.lineno,
                    f"subprocess.{node.func.attr} must be isolated in an approved seam wrapper",
                )
            )
    return diagnostics


def _dynamic_import_boundary_diagnostics(
    tree: ast.AST, relpath: str
) -> list[Diagnostic]:
    if not any(path_matches_prefix(relpath, root) for root in DYNAMIC_IMPORT_ROOTS):
        return []
    if relpath in ADAPTER_IMPORT_ALLOWLIST:
        return []
    diagnostics: list[Diagnostic] = []
    for node in ast.walk(tree):
        if _is_importlib_import_module_call(node):
            diagnostics.append(
                Diagnostic(
                    "PYQ221",
                    relpath,
                    node.lineno,
                    "dynamic importlib.import_module calls must stay in adapter modules",
                )
            )
    return diagnostics


def _contract_boundary_diagnostics(tree: ast.AST, relpath: str) -> list[Diagnostic]:
    if relpath not in CONTRACT_FILES:
        return []
    diagnostics: list[Diagnostic] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign):
            continue
        annotation_names = _annotation_names(node.annotation)
        if "Callable" in annotation_names:
            diagnostics.append(
                Diagnostic(
                    "PYQ230",
                    relpath,
                    node.lineno,
                    "contract dataclass fields must use named Callable aliases",
                )
            )
        if "Any" in annotation_names:
            diagnostics.append(
                Diagnostic(
                    "PYQ231",
                    relpath,
                    node.lineno,
                    "contract dataclass fields must not use Any collaborator boundaries",
                )
            )
    return diagnostics


def class_interface_diagnostics_for_source(
    source: str, relpath: str
) -> list[Diagnostic]:
    """Return class/interface diagnostics for a source string."""
    diagnostics: list[Diagnostic] = []
    tree = ast.parse(source, filename=relpath)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        diagnostics.extend(_class_shape_diagnostics(node, relpath))
        diagnostics.extend(_constructor_diagnostics(node, relpath))
        diagnostics.extend(_analyzer_boundary_diagnostics(node, relpath))

    diagnostics.extend(_subprocess_boundary_diagnostics(tree, relpath))
    diagnostics.extend(_dynamic_import_boundary_diagnostics(tree, relpath))
    diagnostics.extend(_contract_boundary_diagnostics(tree, relpath))
    return diagnostics


def class_interface_diagnostics(
    repo_root: Path, paths: Sequence[Path]
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for path in paths:
        relpath = repo_relative_path(repo_root, path)
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            diagnostics.append(
                Diagnostic(
                    "PYQ299", relpath, 1, f"could not decode Python source: {exc}"
                )
            )
            continue
        diagnostics.extend(class_interface_diagnostics_for_source(source, relpath))
    return diagnostics


def _selected_paths(repo_root: Path, raw_paths: Sequence[str]) -> list[Path]:
    search_roots = (
        [repo_root / raw_path for raw_path in raw_paths] if raw_paths else None
    )
    return discover_unexcluded_python_files(repo_root, search_roots)


def _filter_checks(checks: Sequence[str]) -> set[str]:
    requested = set(checks)
    if "all" in requested:
        return {"structure", "class-interface"}
    return requested


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate maintained Python structure and interface quality."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--check",
        action="append",
        choices=("all", "structure", "class-interface"),
        default=None,
    )
    parser.add_argument("paths", nargs="*")
    parser.add_argument(
        "--print-exclusions",
        action="store_true",
        help="Print deterministic exclusions and exit.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.print_exclusions:
        for rule in EXCLUSION_RULES:
            print(f"{rule.pattern}\t{rule.rationale}")
        return 0

    paths = _selected_paths(repo_root, args.paths)
    checks = _filter_checks(args.check or ["all"])
    diagnostics: list[Diagnostic] = []
    if "structure" in checks:
        diagnostics.extend(structure_diagnostics(repo_root, paths))
    if "class-interface" in checks:
        maintained_paths = [
            path
            for path in paths
            if is_maintained_python_relpath(repo_relative_path(repo_root, path))
        ]
        diagnostics.extend(class_interface_diagnostics(repo_root, maintained_paths))

    if diagnostics:
        print("[python-quality] violations:", file=sys.stderr)
        for diagnostic in sorted(diagnostics, key=lambda item: item.render()):
            print(diagnostic.render(), file=sys.stderr)
        return 1
    print(f"[python-quality] OK: {len(paths)} maintained Python files checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
