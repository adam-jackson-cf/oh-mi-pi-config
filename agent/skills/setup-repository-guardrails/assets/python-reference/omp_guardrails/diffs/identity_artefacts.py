"""TEST.identity-artefacts: test-only work leaves identity-bearing artefacts untouched.

The profile's ``identity_artefacts`` maps each artefact path or pattern to its preservation rule.
Over ``git diff <base> <head>``, when no production file changed, any changed artefact is a
finding. An approved ``{"none": ..., "approval": ...}`` exemption disables the rule.

A changed file is non-production when it is an identity artefact itself, a test file
(``layout.test_file_patterns`` or the house-style defaults), lies under a tier location,
``layout.support_dir`` or ``layout.fixtures_dir``, is the test profile, or matches
``guardrails.identity_artefacts.non_production`` (patterns such as docs). Every other changed
file is production.
"""

from __future__ import annotations

from pathlib import Path

from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.diffs.changes import DiffError, changed_files, parse_range, range_finding
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, matches_any, settings
from omp_guardrails.structure.naming import is_test_file

RULE = "TEST.identity-artefacts"
CHECKER = "identity_artefacts"


def test_locations(profile: dict) -> list[str]:
    locations: list[str] = []
    tiers = profile.get("tiers")
    for tier in tiers.values() if isinstance(tiers, dict) else []:
        if isinstance(tier, dict):
            locations.extend(as_list(tier.get("location")))
    layout = profile.get("layout")
    if isinstance(layout, dict):
        locations.extend(as_list(layout.get("support_dir")))
        locations.extend(as_list(layout.get("fixtures_dir")))
    return locations


def is_production(path: str, profile: dict, artefacts: list[str]) -> bool:
    non_production = [
        PROFILE_PATH,
        *artefacts,
        *test_locations(profile),
        *as_list(settings(profile, CHECKER).get("non_production")),
    ]
    return not (matches_any(path, non_production) or is_test_file(path, profile))


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails diff-check --base <ref> [--head <ref>]``."""
    declared = profile.get("identity_artefacts")
    if not isinstance(declared, dict) or "none" in declared:
        return []
    artefacts = [key for key in declared if isinstance(key, str)]
    try:
        changes = changed_files(repo, parse_range(argv))
    except DiffError as error:
        return [range_finding(RULE, error)]
    paths = [path for change in changes for path in change.paths]
    if any(is_production(path, profile, artefacts) for path in paths):
        return []
    return [
        Finding(
            RULE,
            path,
            "identity-bearing artefact changed in test-only work "
            f"(rule: {declared[next(key for key in artefacts if matches_any(path, [key]))]})",
            "restore the artefact, or make the change in work that also changes production code",
        )
        for path in sorted(set(paths))
        if matches_any(path, artefacts)
    ]
