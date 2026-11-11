"""TEST.golden-change-record: a golden file changes only with a recorded reason.

Over ``git diff <base> <head>``, every modified, deleted, renamed or retyped golden file must be
named (by its repo-relative path) on a line the same diff adds to a change-record file. New
goldens are not changes to an approved output, so additions need no record.

Profile ``guardrails.golden_record`` settings:

- ``globs``: patterns of golden files (e.g. ``["tests/fixtures/golden/**"]``). Without globs the
  repo declares no goldens and the rule has nothing to check.
- ``change_record``: path or pattern of the change-record file(s), e.g. ``docs/golden-changes.md``.
"""

from __future__ import annotations

from pathlib import Path

from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.diffs.changes import (
    Change,
    DiffError,
    changed_files,
    line_changes,
    parse_range,
    range_finding,
)
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, matches_any, settings

RULE = "TEST.golden-change-record"
CHECKER = "golden_record"


def changed_goldens(changes: list[Change], globs: list[str]) -> list[Change]:
    return [
        change
        for change in changes
        if change.status != "A" and any(matches_any(path, globs) for path in change.paths)
    ]


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails diff-check --base <ref> [--head <ref>]``."""
    config = settings(profile, CHECKER)
    globs = as_list(config.get("globs"))
    if not globs:
        return []
    records = as_list(config.get("change_record"))
    if not records:
        return [
            Finding(
                RULE,
                PROFILE_PATH,
                "guardrails.golden_record.globs is set but change_record is missing",
                "name the change-record file in guardrails.golden_record.change_record",
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
