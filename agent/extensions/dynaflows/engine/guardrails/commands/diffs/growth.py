from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.context.changes import DiffError, parse_range, range_finding
from guardrails.context.config.house_style import apply_exceptions
from guardrails.foundation.findings import Finding
from guardrails.toolkit.growth import GROWTH, check


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    try:
        found = check(repo, profile, parse_range(argv))
    except DiffError as error:
        return [range_finding(GROWTH, error)]
    return apply_exceptions(found, profile)
