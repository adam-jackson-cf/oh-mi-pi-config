"""Resolve call targets through import aliases so ``sp.run`` is known as ``subprocess.run``."""

from __future__ import annotations

import ast

PROCESS_CALLS = frozenset({
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.check_call",
    "subprocess.check_output",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "os.system",
    "os.popen",
})
DYNAMIC_IMPORT_CALLS = frozenset({"importlib.import_module", "__import__", "builtins.__import__"})
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def import_names(tree: ast.AST) -> dict[str, str]:
    """Names bound by imports directly in this scope, not in nested functions or classes."""
    names: dict[str, str] = {}
    pending = list(ast.iter_child_nodes(tree))
    while pending:
        node = pending.pop()
        if isinstance(node, SCOPES):
            continue
        pending.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                names[alias.asname or root] = alias.name if alias.asname else root
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                names[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return names


def qualified_name(node: ast.AST, names: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return names.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        return f"{qualified_name(node.value, names)}.{node.attr}"
    return ""
