from __future__ import annotations

import shlex
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from guardrails.commands.adapters.affected import RULE, affected
from guardrails.commands.adapters.go.source import (
    NAME,
    package_patterns,
    selector_arguments,
)
from guardrails.context.changes import DiffError, range_finding
from guardrails.context.config.adapter_declarations import adapter_tiers, claimed
from guardrails.foundation.findings import Finding

SUFFIXES = ("_test.go",)


def affected_arguments(
    repo: Path, profile: dict[str, Any], base: str, head: str
) -> list[Finding]:
    try:
        selection = affected(repo, profile, ["--base", base, "--head", head])
    except DiffError as error:
        return [range_finding(RULE, error)]
    print(f"selection: {selection.reason}", file=sys.stderr)
    paths = claimed(profile, NAME, "t2", selection.paths, SUFFIXES)
    if paths:
        selector = adapter_tiers(profile, NAME).get("t2", {}).get("selector", "")
        folders = sorted({PurePosixPath(path).parent.as_posix() for path in paths})
        print(shlex.join([*selector_arguments(selector), *package_patterns(folders)]))
    return []
