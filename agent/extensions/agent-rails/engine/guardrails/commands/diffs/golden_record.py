from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.context.changes import (
    Change,
    DiffError,
    changed_files,
    line_changes,
    parse_range,
    range_finding,
)
from guardrails.foundation.findings import Finding, config_label
from guardrails.foundation.inventory import as_list, matches_any, settings

RULE = "TEST.golden-change-record"
CHECKER = "golden_record"


def changed_goldens(changes: list[Change], globs: list[str]) -> list[Change]:
    return [
        change
        for change in changes
        if change.status != "A"
        and any(matches_any(path, globs) for path in change.paths)
    ]


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = settings(config, CHECKER)
    globs = as_list(options.get("globs"))
    if not globs:
        return []
    records = as_list(options.get("change_record"))
    if not records:
        return [
            Finding(
                RULE,
                config_label("checkers"),
                "checkers.golden_record.globs is set but change_record is missing",
                "name the change-record file in checkers.golden_record.change_record",
            )
        ]
    try:
        diff = parse_range(argv)
        changes = changed_files(repo, diff)
        goldens = changed_goldens(changes, globs)
        if not goldens:
            return []
        recorded = "\n".join(
            text
            for change in changes
            if change.status != "D" and matches_any(change.path, records)
            for _, text in line_changes(repo, diff, change.path).added
        )
    except DiffError as error:
        return [range_finding(RULE, error)]
    return [
        Finding(
            RULE,
            golden.path,
            f"golden file changed ({golden.status}) without a change-record entry naming it",
            f"add a line naming {golden.path} and the reason to {', '.join(records)}",
        )
        for golden in goldens
        if not any(path in recorded for path in golden.paths)
    ]
