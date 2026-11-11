"""SURFACE.journeys: journeys follow the profile's pattern, call the lever and are mapped.

Journey files are repo files matching ``verification_surface.journey_file_pattern`` (a pattern
without ``/`` matches the file name; with ``/`` it matches the repo-relative path). Each one:

1. references the lever: its text contains the profile's ``lever_command`` or one of the lever
   references (default: the command's path-like tokens and their file names; override with
   ``guardrails.journeys.lever_references`` when journeys reach the lever through a support
   helper, e.g. ``["run_lever("]``);
2. is linked or named (by repo-relative path) from a feature-map document.

Test files inside the pattern's fixed directory prefix that do not match the pattern are
misnamed journeys. A repo may have no journeys (a library maps T1 outcome tests instead).
An approved ``verification_surface`` exemption disables the rule.
"""

from __future__ import annotations

import shlex
from pathlib import Path, PurePosixPath

from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import (
    GLOB_CHARACTERS,
    InventoryError,
    as_list,
    name_matches,
    repo_paths,
    settings,
)
from omp_guardrails.structure.naming import is_test_file
from omp_guardrails.surface.feature_map import (
    map_documents,
    parse,
    read,
    resolve_link,
    surface,
)

RULE = "SURFACE.journeys"
CHECKER = "journeys"


def lever_references(lever: str, profile: dict) -> list[str]:
    configured = as_list(settings(profile, CHECKER).get("lever_references"))
    if configured:
        return configured
    try:
        tokens = shlex.split(lever)
    except ValueError:
        tokens = lever.split()
    references = [lever]
    for token in tokens:
        if "/" in token or (token.count(".") and not token.startswith("-")):
            references.append(token)
            references.append(PurePosixPath(token).name)
    return [reference for reference in dict.fromkeys(references) if reference]


def fixed_prefix(pattern: str) -> str | None:
    """The directory part of a pattern before its first glob segment, if it has one."""
    parts = PurePosixPath(pattern).parts[:-1]
    fixed = []
    for part in parts:
        if GLOB_CHARACTERS & set(part):
            break
        fixed.append(part)
    return "/".join(fixed) or None


def mapped_paths(repo: Path, map_path: str) -> tuple[set[str], str]:
    """Files the feature map links to, and the map's combined text for path mentions."""
    linked: set[str] = set()
    texts = []
    root = repo / map_path
    if not map_path or not root.exists():
        return linked, ""
    for doc in map_documents(repo, map_path):
        text = read(repo / doc)
        texts.append(text)
        for target in parse(text).links:
            resolved, _ = resolve_link(repo, doc, target)
            if resolved:
                linked.add(resolved)
    return linked, "\n".join(texts)


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails surface-lint``."""
    del argv
    values = surface(profile)
    if values is None:
        return []
    pattern = str(values.get("journey_file_pattern") or "").strip()
    lever = str(values.get("lever_command") or "").strip()
    if not pattern or not lever:
        return [
            Finding(
                RULE,
                ".omp/test-profile.md",
                "verification_surface needs journey_file_pattern and lever_command",
                "record both in the test profile",
            )
        ]
    try:
        paths = repo_paths(repo)
    except InventoryError as error:
        return [Finding(RULE, ".", str(error), "run inside a git repository")]
    journeys = [path for path in paths if name_matches(path, pattern)]
    references = lever_references(lever, profile)
    linked, map_text = mapped_paths(repo, str(values.get("feature_map_path") or "").rstrip("/"))
    found: list[Finding] = []
    for journey in journeys:
        if not any(reference in read(repo / journey) for reference in references):
            found.append(
                Finding(
                    RULE,
                    journey,
                    f"journey does not call the lever (looked for {references})",
                    "drive the feature through the lever command",
                )
            )
        if journey not in linked and journey not in map_text:
            found.append(
                Finding(
                    RULE,
                    journey,
                    "journey is not linked from any feature-map document",
                    "link the journey from its feature's 'Driving it with' section",
                )
            )
    prefix = fixed_prefix(pattern)
    if prefix:
        found.extend(
            Finding(
                RULE,
                path,
                f"test file in the journey directory does not match '{pattern}'",
                "rename it to the journey file pattern or move it to its tier's location",
            )
            for path in paths
            if path.startswith(f"{prefix}/")
            and path not in journeys
            and is_test_file(path, profile)
        )
    return found
