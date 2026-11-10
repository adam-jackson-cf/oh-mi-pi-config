"""LSP readiness: detect the repo's stacks, name their language servers, check the PATH.

Stacks are detected from root markers in the repo root only, the way omp's LSP client finds
them. The server names follow omp's built-in defaults (``omp://lsp-config.md``). Init records
each server and its install hint in the profile's ``command_notes.lsp``; a server missing from
``PATH`` is a Project setup gap (``PROFILE.complete``) unless the caller allows it.

The canary ``references`` query runs through omp's ``lsp`` tool, which this package cannot
call; the setup skill runs it after init and records the result next to these notes.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.findings import Finding

RULE = "PROFILE.complete"


@dataclass(frozen=True)
class Stack:
    name: str
    markers: tuple[str, ...]
    server: str
    install: str


STACKS = (
    Stack(
        "python",
        ("pyproject.toml",),
        "pyright-langserver",
        "install pyright (npm install -g pyright, or uv tool install pyright)",
    ),
    Stack(
        "typescript",
        ("package.json", "tsconfig.json"),
        "typescript-language-server",
        "npm install -g typescript typescript-language-server",
    ),
)


def detect(repo: Path) -> list[Stack]:
    return [stack for stack in STACKS if any((repo / name).is_file() for name in stack.markers)]


def notes(stacks: list[Stack]) -> list[dict]:
    return [
        {"stack": stack.name, "server": stack.server, "install": stack.install} for stack in stacks
    ]


def readiness(stacks: list[Stack], *, allow_missing: bool) -> tuple[list[Finding], list[str]]:
    """Findings for missing servers (or report lines when allowed) and lines for present ones."""
    findings: list[Finding] = []
    lines: list[str] = []
    for stack in stacks:
        found = shutil.which(stack.server)
        if found:
            lines.append(f"lsp: {stack.name} server {stack.server} found at {found}")
            continue
        message = f"{stack.name} language server {stack.server} is not on PATH"
        if allow_missing:
            lines.append(f"lsp: {message} (allowed by --allow-missing-lsp); {stack.install}")
        else:
            findings.append(
                Finding(RULE, PROFILE_PATH, message, f"{stack.install}, then run init again")
            )
    if not stacks:
        lines.append("lsp: no pyproject.toml, package.json or tsconfig.json in the repo root")
    return findings, lines
