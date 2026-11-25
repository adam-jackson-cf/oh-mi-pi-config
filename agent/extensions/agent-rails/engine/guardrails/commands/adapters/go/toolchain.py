"""The Go toolchain the adapter drives: its executable and how selectors and locations become go test arguments."""

from __future__ import annotations

import shlex
import shutil
from typing import Any

from guardrails.foundation.inventory import settings

NAME = "go"
SETTINGS = "adapter_go"


class ToolchainError(ValueError):
    pass


def command(config: dict[str, Any]) -> list[str]:
    configured = settings(config, SETTINGS).get("command")
    if configured is not None:
        if (
            not isinstance(configured, list)
            or not configured
            or not all(isinstance(part, str) for part in configured)
        ):
            raise ToolchainError(
                f"checkers.{SETTINGS}.command must be a nonempty argv list"
            )
        return configured
    go = shutil.which("go")
    if go is None:
        raise ToolchainError(
            f"go is not on PATH; activate the repository's Go or set "
            f"checkers.{SETTINGS}.command in .agentrails/config.json"
        )
    return [go]


def selector_arguments(selector: str) -> list[str]:
    return shlex.split(selector)


def package_patterns(locations: list[str]) -> list[str]:
    patterns = []
    for location in locations:
        cleaned = location.strip().rstrip("/") or "."
        if cleaned == "." or cleaned.startswith(("./", "../", "/")):
            patterns.append(cleaned if cleaned != "." else "./")
        else:
            patterns.append(f"./{cleaned}")
    return patterns
