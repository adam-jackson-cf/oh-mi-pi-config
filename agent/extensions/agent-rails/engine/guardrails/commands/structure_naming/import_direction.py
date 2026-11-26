from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.structure_naming.import_graph import (
    DIRECTION,
    Reader,
    graph_language,
    import_config,
    import_settings,
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


def owner_tier(path: str, root: str) -> str | None:
    if not matches(path, root):
        return None
    parts = PurePosixPath(path).relative_to(root).parts
    return parts[0] if len(parts) > 1 else None


def allowed(owner: str | None, destination: str | None, tiers: list[str]) -> bool:
    return (
        owner in tiers
        and destination in tiers
        and tiers.index(destination) <= tiers.index(owner)
    )


def direction_findings(repo: Path, path: str, config: dict[str, Any]) -> list[Finding]:
    settings = import_settings(config)
    language = graph_language(path)
    if (
        settings is None
        or language is None
        or not matches_any(path, settings.scan)
        or not (repo / path).is_file()
    ):
        return []
    edges = Reader(repo, settings).edges(path, language)
    if isinstance(edges, Finding):
        return [edges]
    owner = owner_tier(path, settings.root)
    return [
        Finding(
            DIRECTION,
            path,
            f"unmapped or upward import {edge.label}",
            "import only your own or a lower tier from structure.tiers",
        )
        for edge in sorted(edges, key=lambda edge: edge.label)
        if not allowed(owner, edge.tier, settings.tiers)
    ]


def native_findings(
    repo: Path, paths: list[str], config: dict[str, Any]
) -> list[Finding]:
    direction, _ = import_config(config)
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
