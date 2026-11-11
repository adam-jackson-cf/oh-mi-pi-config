"""TEST.growth.justified and TEST.duplicate-path over ``git diff <base> <head>``.

``omp-guardrails diff-check --base <ref> [--head <ref>]`` collects the test files at both
commits through the profile adapter's static case collector (``omp_guardrails.adapters.
<adapter>.cases``, for pytest ``omp_guardrails.adapters.pytest.cases``). Test files are files
matching ``layout.test_file_patterns`` (the house-style defaults when the profile has none).

- TEST.growth.justified: every test function added at head needs an inline ``behaviour:``
  comment in the test language's comment syntax (Python ``# behaviour: <text>``), stating the
  behaviour the test proves. One comment covers the function and all its parameter rows. The
  check is presence only: a missing comment or placeholder text (empty, ``TBD``, ``<...>``) is
  a finding naming the test, fixed by adding the comment or removing the test. Whether the text
  is true is judged outside this checker, on a sample (Jev ``judge_batch()``). A changed test
  file the adapter cannot collect statically is a finding too.
- TEST.duplicate-path: a case added at head whose fingerprint equals an existing case or an
  earlier added case is a finding naming both, fixed by merging or removing one.

A finding is waived only by an ADR-backed ``house_style.exceptions`` entry for its rule.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from omp_guardrails.config.house_style import apply_exceptions
from omp_guardrails.config.profile import is_missing_text
from omp_guardrails.diffs.changes import (
    DiffError,
    DiffRange,
    changed_files,
    git,
    parse_range,
    range_finding,
)
from omp_guardrails.findings import Finding
from omp_guardrails.structure.naming import is_test_file

GROWTH = "TEST.growth.justified"
DUPLICATE = "TEST.duplicate-path"
ADAPTER_NAME = re.compile(r"[a-z][a-z0-9_]*")
BEHAVIOUR_FIX = (
    "add an inline 'behaviour:' comment stating what the test proves, or remove the test"
)
DUPLICATE_FIX = "merge or remove one of the duplicate tests"


@dataclass(frozen=True)
class Snapshot:
    """The statically collected tests of one commit."""

    fingerprints: dict[str, str]
    behaviours: dict[str, str | None]
    errors: dict[str, str]


Collector = Callable[[str, str], object]


def adapter_collector(profile: dict) -> Collector | None:
    """The adapter's ``cases.collect``, or None when the adapter ships no static collector."""
    name = profile.get("adapter")
    if not isinstance(name, str) or not ADAPTER_NAME.fullmatch(name):
        return None
    try:
        module = importlib.import_module(f"omp_guardrails.adapters.{name}.cases")
    except ModuleNotFoundError:
        return None
    return getattr(module, "collect", None)


def test_blobs(repo: Path, ref: str, profile: dict) -> dict[str, str]:
    """``{path: blob id}`` of the test files at ``ref``."""
    output = git(repo, "ls-tree", "-r", "-z", ref)
    blobs = {}
    for entry in output.split("\0"):
        if not entry or "\t" not in entry:
            continue
        meta, path = entry.split("\t", 1)
        kind, blob = meta.split()[1:3]
        if kind == "blob" and is_test_file(path, profile):
            blobs[path] = blob
    return blobs


def snapshot(
    repo: Path, blobs: Mapping[str, str], collect: Collector, cache: dict[str, object]
) -> Snapshot:
    """Collect each file once per blob, so files unchanged between commits parse once."""
    fingerprints: dict[str, str] = {}
    behaviours: dict[str, str | None] = {}
    errors: dict[str, str] = {}
    for path, blob in sorted(blobs.items()):
        key = f"{path}\0{blob}"
        if key not in cache:
            try:
                cache[key] = collect(path, git(repo, "cat-file", "blob", blob))
            except ValueError as error:
                cache[key] = error
        result = cache[key]
        if isinstance(result, ValueError):
            errors[path] = str(result)
            continue
        behaviours.update(result.behaviours)
        fingerprints.update((case.selector, case.fingerprint) for case in result.cases)
    return Snapshot(fingerprints, behaviours, errors)


def growth_findings(base: Snapshot, head: Snapshot) -> list[Finding]:
    """Test functions new at head without a real inline behaviour comment."""
    found = []
    for selector in sorted(head.behaviours.keys() - base.behaviours.keys()):
        text = head.behaviours[selector]
        if text is None:
            problem = "has no inline 'behaviour:' comment"
        elif is_missing_text(text):
            problem = f"has a placeholder 'behaviour:' comment ({text!r})"
        else:
            continue
        path = selector.split("::", 1)[0]
        found.append(Finding(GROWTH, path, f"{selector} {problem}", BEHAVIOUR_FIX))
    return found


def duplicate_findings(base: Mapping[str, str], head: Mapping[str, str]) -> list[Finding]:
    """New cases whose fingerprint matches an existing case or an earlier new case."""
    seen: dict[str, str] = {}
    for selector, fingerprint in sorted(base.items()):
        seen.setdefault(fingerprint, selector)
    for selector in sorted(head.keys() & base.keys()):
        seen.setdefault(head[selector], selector)
    found = []
    for selector in sorted(head.keys() - base.keys()):
        fingerprint = head[selector]
        if fingerprint in seen:
            path = selector.split("::", 1)[0]
            message = f"{selector} duplicates the call path and assertions of {seen[fingerprint]}"
            found.append(Finding(DUPLICATE, path, message, DUPLICATE_FIX))
        else:
            seen[fingerprint] = selector
    return found


def collection_findings(head: Snapshot, changed: set[str]) -> list[Finding]:
    return [
        Finding(GROWTH, path, f"cannot collect tests statically: {error}", BEHAVIOUR_FIX)
        for path, error in sorted(head.errors.items())
        if path in changed
    ]


def check(repo: Path, profile: dict, diff: DiffRange) -> list[Finding]:
    changes = changed_files(repo, diff)
    changed_paths = {path for change in changes for path in change.paths}
    if not any(is_test_file(path, profile) for path in changed_paths):
        return []
    collect = adapter_collector(profile)
    if collect is None:
        return [
            Finding(
                GROWTH,
                ".omp/test-profile.md",
                f"adapter {profile.get('adapter')!r} has no static test-case collector",
                "name an adapter that ships one, or add 'cases.collect' to the adapter",
            )
        ]
    cache: dict[str, object] = {}
    base = snapshot(repo, test_blobs(repo, diff.base, profile), collect, cache)
    head = snapshot(repo, test_blobs(repo, diff.head, profile), collect, cache)
    found = collection_findings(head, changed_paths)
    found.extend(growth_findings(base, head))
    found.extend(duplicate_findings(base.fingerprints, head.fingerprints))
    return found


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails diff-check --base <ref> [--head <ref>]``."""
    try:
        found = check(repo, profile, parse_range(argv))
    except DiffError as error:
        return [range_finding(GROWTH, error)]
    return apply_exceptions(found, profile)
