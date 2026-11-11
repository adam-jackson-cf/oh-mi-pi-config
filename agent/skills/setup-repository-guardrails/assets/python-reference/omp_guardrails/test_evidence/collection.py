"""Collection evidence: run-report hygiene and TEST.tier-selectors over per-tier test lists.

Run report (called by ``propagation`` under ``report-check``, on the commit and CI gates):

- An empty report (no test cases) never passes: ``TEST.failure-propagation``.
- A test ID reported more than once makes the evidence ambiguous: ``TEST.failure-propagation``.
- A skipped or xfail case needs an approved entry: ``TEST.no-evasion``.
- A case that passed only after retries (retry markers, or an earlier failing duplicate)
  is a retry to green: ``TEST.no-evasion``.

Tier collections (``omp-guardrails collection-check --tier t1=PATH --tier t2=PATH ...``):
each PATH is the adapter's collect-only list for that tier, one normalised test ID
(``<repo-relative file>::<name>``) per line; blank lines and ``#`` comments are ignored.

- Every profile tier needs a list; a missing or unreadable list, or an empty one, is
  ``TEST.failure-propagation`` (empty collection never passes).
- ``TEST.tier-selectors``: an ID listed twice, a file selected by two tiers (one tier per
  file), and a repository file matching ``layout.test_file_patterns`` that no tier selects.

Profile ``guardrails.test_evidence`` settings (all optional):

- ``approved_skips``: ``[{"id": "<glob over test IDs>", "reason": "...", "approval": "..."}]``;
  an entry without a real reason and approval approves nothing.
- ``unselected_exempt``: path patterns that are test-named but are not test files.
"""

from __future__ import annotations

from collections import Counter
from fnmatch import fnmatchcase
from pathlib import Path

from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import (
    InventoryError,
    as_list,
    matches_any,
    name_matches,
    repo_paths,
)
from omp_guardrails.test_evidence.junit import Report, UsageError, evidence_settings

PROPAGATION = "TEST.failure-propagation"
EVASION = "TEST.no-evasion"
SELECTORS = "TEST.tier-selectors"
SKIP_FIX = (
    "make the test run and pass, or record an approved_skips entry with the owner's "
    "reason and approval reference"
)
RETRY_FIX = "fix the flake's cause so it passes on the first run; never rely on retries"
DUPLICATE_FIX = "give every test a unique ID so each result maps to one test"
EMPTY_FIX = "check the tier selector and command; an empty collection never passes"


def approved(test_id: str, profile: dict) -> bool:
    entries = evidence_settings(profile).get("approved_skips")
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        pattern, reason, approval = (entry.get(key) for key in ("id", "reason", "approval"))
        if not all(isinstance(value, str) and value.strip() for value in (pattern, reason, approval)):
            continue
        if fnmatchcase(test_id, pattern):
            return True
    return False


def report_findings(report: Report, profile: dict) -> list[Finding]:
    """Empty, duplicated, unapproved-skip and retried-to-green evidence in one run report."""
    if not report.cases:
        return [Finding(PROPAGATION, report.source, "run report holds no test cases", EMPTY_FIX)]
    found = []
    by_id: dict[str, list] = {}
    for case in report.cases:
        by_id.setdefault(case.id, []).append(case)
    for test_id, cases in by_id.items():
        path = cases[-1].file or report.source
        final = cases[-1]
        earlier_failed = any(case.outcome in {"failed", "error"} for case in cases[:-1])
        if final.outcome == "passed" and (final.retries > 0 or earlier_failed):
            attempts = final.retries + len(cases) - 1
            found.append(
                Finding(EVASION, path, f"{test_id} passed only after {attempts} retries", RETRY_FIX)
            )
        elif len(cases) > 1:
            found.append(
                Finding(
                    PROPAGATION,
                    path,
                    f"{test_id} is reported {len(cases)} times",
                    DUPLICATE_FIX,
                )
            )
        if final.outcome in {"skipped", "xfail"} and not approved(test_id, profile):
            reason = f" ({final.reason})" if final.reason else ""
            found.append(
                Finding(EVASION, path, f"{test_id} is {final.outcome} without approval{reason}", SKIP_FIX)
            )
    return found


def tier_arguments(argv: list[str]) -> dict[str, str]:
    lists: dict[str, str] = {}
    index = 0
    while index < len(argv):
        flag, _, inline = argv[index].partition("=")
        if flag == "--tier":
            value = inline or (argv[index + 1] if index + 1 < len(argv) else "")
            index += 0 if inline else 1
            tier, sep, path = value.partition("=")
            if not sep or not tier or not path:
                raise UsageError("--tier takes <tier>=<collect-only list path>")
            if tier in lists:
                raise UsageError(f"--tier {tier} given twice")
            lists[tier] = path
        index += 1
    return lists


def read_ids(path: Path) -> list[str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def id_file(test_id: str) -> str:
    return test_id.split("::", 1)[0]


def profile_tiers(profile: dict) -> list[str]:
    tiers = profile.get("tiers")
    return sorted(tiers) if isinstance(tiers, dict) else []


def collection_findings(
    collections: dict[str, list[str] | None],
    profile: dict,
    test_files: list[str],
) -> list[Finding]:
    """TEST.tier-selectors and empty-collection findings for per-tier ID lists."""
    found = []
    file_tiers: dict[str, set[str]] = {}
    for tier in profile_tiers(profile):
        if tier not in collections:
            found.append(
                Finding(
                    SELECTORS,
                    tier,
                    f"tier {tier} has no collect-only list",
                    f"pass --tier {tier}=<list> from the adapter's tier listing",
                )
            )
    for tier, ids in sorted(collections.items()):
        if tier not in profile_tiers(profile):
            found.append(
                Finding(SELECTORS, tier, f"tier {tier} is not a profile tier", "use a profile tier key")
            )
            continue
        if ids is None:
            found.append(
                Finding(PROPAGATION, tier, f"tier {tier} collect-only list is missing", EMPTY_FIX)
            )
            continue
        if not ids:
            found.append(Finding(PROPAGATION, tier, f"tier {tier} collected no tests", EMPTY_FIX))
        for test_id, count in sorted(Counter(ids).items()):
            if count > 1:
                found.append(
                    Finding(
                        SELECTORS,
                        id_file(test_id),
                        f"{test_id} is collected {count} times in tier {tier}",
                        DUPLICATE_FIX,
                    )
                )
        for test_id in ids:
            file_tiers.setdefault(id_file(test_id), set()).add(tier)
    for file, tiers in sorted(file_tiers.items()):
        if len(tiers) > 1:
            found.append(
                Finding(
                    SELECTORS,
                    file,
                    f"selected by tiers {', '.join(sorted(tiers))}",
                    "make the tier selectors disjoint: one tier per test file",
                )
            )
    if all(ids is not None for ids in collections.values()):
        for file in test_files:
            if file not in file_tiers:
                found.append(
                    Finding(
                        SELECTORS,
                        file,
                        "test file is selected by no tier",
                        "move it under a tier location or fix the tier selectors",
                    )
                )
    return found


def repository_test_files(repo: Path, profile: dict) -> list[str]:
    layout = profile.get("layout")
    patterns = as_list(layout.get("test_file_patterns")) if isinstance(layout, dict) else []
    exempt = as_list(evidence_settings(profile).get("unselected_exempt"))
    return [
        path
        for path in repo_paths(repo)
        if any(name_matches(path, pattern) for pattern in patterns)
        and not matches_any(path, exempt)
        and (repo / path).is_file()
    ]


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails collection-check --tier <tier>=<list> ...`` (one per profile tier)."""
    try:
        lists = tier_arguments(argv)
    except UsageError as error:
        return [Finding(SELECTORS, "collection-check", f"usage: {error}", "fix the arguments")]
    collections = {tier: read_ids(repo / path) for tier, path in lists.items()}
    try:
        test_files = repository_test_files(repo, profile)
    except InventoryError as error:
        return [
            Finding(SELECTORS, "collection-check", f"cannot list test files: {error}", "run inside a git work tree")
        ]
    return collection_findings(collections, profile, test_files)
