from __future__ import annotations

import ast
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure.import_graph import (
    DIRECTION,
    import_config,
    imported_edges,
)
from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding
from guardrails.foundation.inventory import (
    as_list,
    matches,
    matches_any,
    normalized_path,
)
from guardrails.host.process import run_text


def direction_findings(repo: Path, path: str, profile: dict[str, Any]) -> list[Finding]:
    direction, tiers = import_config(profile)
    package, source_root = direction.get("package"), direction.get("source_root")
    if not (
        isinstance(package, str)
        and isinstance(source_root, str)
        and path.endswith(".py")
    ):
        return []
    if not matches_any(path, as_list(direction.get("scan")) or [source_root]):
        return []
    source = repo / path
    if not source.is_file():
        return []
    inside = matches(path, source_root)
    relative = (
        PurePosixPath(path).relative_to(source_root.rstrip("/")) if inside else None
    )
    module = (
        ".".join([package, *relative.with_suffix("").parts]) if relative else package
    )
    try:
        tree = ast.parse(source.read_bytes(), filename=path)
        edges = imported_edges(tree, module, package)
    except (OSError, SyntaxError, ValueError, ImportError) as error:
        return [Finding(DIRECTION, path, str(error), "restore readable Python imports")]
    owner = relative.parts[0] if relative and len(relative.parts) > 1 else None
    found = []
    for edge in edges:
        destination = edge.removeprefix(f"{package}.").split(".")[0]
        allowed = (
            owner in tiers
            and destination in tiers
            and tiers.index(destination) <= tiers.index(owner)
        )
        if not allowed:
            found.append(
                Finding(
                    DIRECTION,
                    path,
                    f"unmapped or upward import {edge}",
                    "import only your own or a lower tier from structure.tiers",
                )
            )
    return found


def native_findings(
    repo: Path, paths: list[str], profile: dict[str, Any]
) -> list[Finding]:
    direction, _ = import_config(profile)
    native = direction.get("native")
    scan = as_list(direction.get("scan")) or as_list(direction.get("source_root"))
    sources = sorted({
        path
        for raw in paths
        if (path := normalized_path(repo, raw)) and matches_any(path, scan)
    })
    if not sources or not isinstance(native, dict):
        return []
    command, timeout = as_list(native.get("command")), native.get("timeout_s")
    if not command or not isinstance(timeout, (int, float)):
        problem = "structure.import_direction.native needs command and timeout_s"
    else:
        try:
            result = run_text(
                command,
                cwd=repo / native.get("cwd", "."),
                env=repo_environment(),
                check=False,
                timeout=timeout,
            )
            if result.returncode == 0:
                return []
            problem = "repository import contracts failed"
        except (OSError, subprocess.TimeoutExpired) as error:
            problem = f"cannot run the import-contract engine: {error}"
    return [
        Finding(
            DIRECTION,
            path,
            problem,
            f"run {' '.join(command)} and remove forbidden edges",
        )
        for path in sources
    ]
