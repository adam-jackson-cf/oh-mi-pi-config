#!/usr/bin/env python3
"""Fail when Python modules have broad architectural fan-out."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

# Generic generated/cache exclusions; adapt additional exclusions per target.
DEFAULT_EXCLUDED_PREFIXES = (
    ".build/",
    ".git/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "node_modules/",
)


@dataclass(frozen=True)
class SourceRoot:
    path: Path
    relpath: str


@dataclass(frozen=True)
class PythonFile:
    path: Path
    relpath: str
    module: str | None
    is_package: bool


@dataclass(frozen=True)
class FanoutLimits:
    architectural_max: int
    large_module_nloc: int
    large_module_architectural_max: int


@dataclass(frozen=True)
class FanoutResult:
    relpath: str
    count: int
    limit: int
    nloc: int
    dependencies: tuple[str, ...]
    reasons: tuple[str, ...]


def _run_git(
    repo_root: Path,
    args: list[str],
    *,
    input_text: str | None = None,
    allowed_return_codes: tuple[int, ...] = (0,),
) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode not in allowed_return_codes:
        raise SystemExit(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def _gitignored_paths(repo_root: Path, relpaths: list[str]) -> set[str]:
    if not relpaths:
        return set()
    stdout = _run_git(
        repo_root,
        ["check-ignore", "-z", "--no-index", "--stdin"],
        input_text="\0".join(relpaths) + "\0",
        allowed_return_codes=(0, 1),
    )
    return {path for path in stdout.split("\0") if path}


def _canonical_relpath(repo_root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise SystemExit(f"path is outside repo root: {path}") from exc


def _is_excluded(relpath: str) -> bool:
    return relpath.startswith(DEFAULT_EXCLUDED_PREFIXES)


def _normalise_source_roots(
    repo_root: Path, values: list[str]
) -> tuple[SourceRoot, ...]:
    roots: list[SourceRoot] = []
    for value in values:
        root_path = (repo_root / value).resolve()
        if not root_path.is_dir():
            raise SystemExit(f"source root does not exist: {value}")
        relpath = _canonical_relpath(repo_root, root_path)
        if _is_excluded(f"{relpath}/"):
            raise SystemExit(f"source root is excluded: {value}")
        roots.append(SourceRoot(path=root_path, relpath=relpath))
    return tuple(sorted(roots, key=lambda item: len(item.path.parts), reverse=True))


def discover_python_files(
    repo_root: Path,
    *,
    source_roots: tuple[SourceRoot, ...],
) -> list[Path]:
    pathspecs = [
        ":(glob)**/*.py" if root.relpath == "." else f":(glob){root.relpath}/**/*.py"
        for root in source_roots
    ]
    output = _run_git(
        repo_root,
        [
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
            "--",
            *pathspecs,
        ],
    )
    relpaths = sorted(path for path in output.split("\0") if path)
    ignored = _gitignored_paths(repo_root, relpaths)
    discovered: list[Path] = []
    for relpath in relpaths:
        path = repo_root / relpath
        if (
            relpath not in ignored
            and not _is_excluded(relpath)
            and not path.is_symlink()
            and path.is_file()
        ):
            discovered.append(path)
    return discovered


def _module_for_path(
    path: Path, source_roots: tuple[SourceRoot, ...]
) -> tuple[str | None, bool]:
    for source_root in source_roots:
        try:
            rel = path.resolve().relative_to(source_root.path)
        except ValueError:
            continue
        if rel.name == "__init__.py":
            parts = rel.parts[:-1]
            return (".".join(parts) if parts else None), True
        module_parts = (*rel.parts[:-1], rel.stem)
        return ".".join(module_parts), False
    return None, False


def collect_python_files(
    repo_root: Path,
    *,
    source_roots: tuple[SourceRoot, ...],
) -> list[PythonFile]:
    files: list[PythonFile] = []
    for path in discover_python_files(repo_root, source_roots=source_roots):
        module, is_package = _module_for_path(path, source_roots)
        files.append(
            PythonFile(
                path=path,
                relpath=path.relative_to(repo_root).as_posix(),
                module=module,
                is_package=is_package,
            )
        )
    return files


def _resolve_relative_import(current: PythonFile, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    if current.module is None:
        return None
    package_parts = (
        current.module.split(".")
        if current.is_package
        else current.module.split(".")[:-1]
    )
    if node.level > 1:
        package_parts = package_parts[: -(node.level - 1)]
    if node.module:
        package_parts.extend(node.module.split("."))
    return ".".join(package_parts)


def _normalise_known_module(
    module: str,
    *,
    root_packages: set[str],
    known_modules: set[str],
) -> str:
    root = module.split(".", 1)[0]
    if root not in root_packages:
        return module
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        candidate = ".".join(parts[:end])
        if candidate in known_modules:
            return candidate
    return root


def imported_modules(
    source: str,
    current: PythonFile,
    *,
    root_packages: set[str],
    known_modules: set[str],
) -> set[str]:
    tree = ast.parse(source, filename=current.relpath)
    imports: set[str] = set()
    for node in ast.walk(tree):
        imports.update(
            _imports_from_node(
                node,
                current,
                root_packages=root_packages,
                known_modules=known_modules,
            )
        )
    if current.module is not None:
        imports.discard(current.module)
    return imports


def _imports_from_node(
    node: ast.AST,
    current: PythonFile,
    *,
    root_packages: set[str],
    known_modules: set[str],
) -> set[str]:
    dynamic_import = _literal_importlib_import_module(node)
    if dynamic_import is not None:
        return {
            _normalise_known_module(
                dynamic_import,
                root_packages=root_packages,
                known_modules=known_modules,
            )
        }
    if isinstance(node, ast.Import):
        return {
            _normalise_known_module(
                alias.name,
                root_packages=root_packages,
                known_modules=known_modules,
            )
            for alias in node.names
        }
    if isinstance(node, ast.ImportFrom):
        return _imports_from_import_from(
            node,
            current,
            root_packages=root_packages,
            known_modules=known_modules,
        )
    return set()


def _imports_from_import_from(
    node: ast.ImportFrom,
    current: PythonFile,
    *,
    root_packages: set[str],
    known_modules: set[str],
) -> set[str]:
    module = _resolve_relative_import(current, node)
    if module is None:
        return set()
    root = module.split(".", 1)[0]
    if root not in root_packages:
        return {module}

    imports: set[str] = set()
    for alias in node.names:
        candidate = module if alias.name == "*" else f"{module}.{alias.name}"
        dependency = _normalise_known_module(
            candidate,
            root_packages=root_packages,
            known_modules=known_modules,
        )
        if candidate not in known_modules:
            dependency = _normalise_known_module(
                module,
                root_packages=root_packages,
                known_modules=known_modules,
            )
        imports.add(dependency)
    return imports


def _literal_importlib_import_module(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "import_module":
        return None
    if not isinstance(node.func.value, ast.Name) or node.func.value.id != "importlib":
        return None
    module_name = _literal_string_argument(node, position=0, keyword="name")
    if module_name is None or not module_name.startswith("."):
        return module_name
    package = _literal_string_argument(node, position=1, keyword="package")
    if package is None:
        return None
    return _resolve_relative_dynamic_import(module_name, package)


def _literal_string_argument(
    node: ast.Call, *, position: int, keyword: str
) -> str | None:
    argument = (
        node.args[position]
        if len(node.args) > position
        else next(
            (argument.value for argument in node.keywords if argument.arg == keyword),
            None,
        )
    )
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _resolve_relative_dynamic_import(module_name: str, package: str) -> str | None:
    if not package:
        return None
    level = len(module_name) - len(module_name.lstrip("."))
    package_parts = package.split(".")
    if level > len(package_parts):
        return None
    base = package_parts[: len(package_parts) - level + 1]
    suffix = module_name[level:]
    return ".".join((*base, suffix)) if suffix else ".".join(base)


def _is_stdlib_module(module: str) -> bool:
    root = module.split(".", 1)[0]
    return root == "__future__" or root in sys.stdlib_module_names


def architectural_dependencies(imports: set[str]) -> set[str]:
    return {module for module in imports if not _is_stdlib_module(module)}


def source_nloc(source: str) -> int:
    return sum(
        1
        for line in source.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def analyse_fanout(
    repo_root: Path,
    *,
    source_roots: tuple[SourceRoot, ...],
    root_packages: set[str],
    limits: FanoutLimits,
) -> list[FanoutResult]:
    files = collect_python_files(repo_root, source_roots=source_roots)
    known_modules = {python_file.module for python_file in files if python_file.module}
    for module in tuple(known_modules):
        parts = module.split(".")
        known_modules.update(
            ".".join(parts[:parent_end]) for parent_end in range(1, len(parts))
        )
    known_modules.update(root_packages)
    results: list[FanoutResult] = []
    for python_file in files:
        source = python_file.path.read_text(encoding="utf-8")
        dependencies = architectural_dependencies(
            imported_modules(
                source,
                python_file,
                root_packages=root_packages,
                known_modules=known_modules,
            )
        )
        nloc = source_nloc(source)
        reasons: list[str] = []
        if len(dependencies) > limits.architectural_max:
            reasons.append(
                f"architectural imports {len(dependencies)} > {limits.architectural_max}"
            )
        if (
            nloc > limits.large_module_nloc
            and len(dependencies) > limits.large_module_architectural_max
        ):
            reasons.append(
                f"large module {nloc} NLOC with architectural imports "
                f"{len(dependencies)} > {limits.large_module_architectural_max}"
            )
        results.append(
            FanoutResult(
                relpath=python_file.relpath,
                count=len(dependencies),
                limit=limits.architectural_max,
                nloc=nloc,
                dependencies=tuple(sorted(dependencies)),
                reasons=tuple(reasons),
            )
        )
    return results


def _root_packages(values: list[str]) -> set[str]:
    packages = set()
    for value in values:
        package = value.strip()
        if not package or PurePosixPath(package).parts != (package,):
            raise SystemExit(
                f"root package must be a single Python package name: {value}"
            )
        packages.add(package)
    if not packages:
        raise SystemExit("at least one --root-package is required")
    return packages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--source-root",
        action="append",
        required=True,
        help="Repo-relative Python source root to inspect. May be provided more than once.",
    )
    parser.add_argument(
        "--root-package",
        action="append",
        required=True,
        help="Root package name treated as first-party architectural surface.",
    )
    parser.add_argument("--architectural-max", type=int, default=10)
    parser.add_argument("--large-module-nloc", type=int, default=800)
    parser.add_argument("--large-module-architectural-max", type=int, default=3)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    source_roots = _normalise_source_roots(repo_root, args.source_root)
    results = analyse_fanout(
        repo_root,
        source_roots=source_roots,
        root_packages=_root_packages(args.root_package),
        limits=FanoutLimits(
            architectural_max=args.architectural_max,
            large_module_nloc=args.large_module_nloc,
            large_module_architectural_max=args.large_module_architectural_max,
        ),
    )

    failures = [result for result in results if result.reasons]
    if not failures:
        print(f"[fanout] OK: {len(results)} Python files checked")
        return 0

    print("[fanout] Architectural fan-out limit exceeded:", file=sys.stderr)
    for result in sorted(failures, key=lambda item: (-item.count, item.relpath)):
        dependencies = ", ".join(result.dependencies)
        reasons = "; ".join(result.reasons)
        print(
            f"  {result.relpath}: {reasons} ({dependencies})",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
