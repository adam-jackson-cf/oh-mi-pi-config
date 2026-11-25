from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from guardrails.context.changes import DiffError, range_finding
from guardrails.foundation.findings import Finding
from guardrails.toolkit.affected import RULE, affected


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        selection = affected(repo, config, argv)
    except DiffError as error:
        return [range_finding(RULE, error)]
    print(f"selection: {selection.reason}", file=sys.stderr)
    for path in selection.paths:
        print(path)
    return []
