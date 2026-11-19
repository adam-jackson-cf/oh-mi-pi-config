"""The Bun adapter: evidence for the shared test checkers from Bun's built-in ``bun test``.

``guardrails adapter bun_test <action>`` when the profile's ``adapters`` include ``bun_test``:

- ``lint [PATH ...]`` (default): source rules for ``bun:test`` suites (``lint``).
- ``tiers --out DIR [--tier T ...]``: collect-only test IDs per tier into ``DIR/<tier>.txt``
  (``tiers``).
- ``junit-args --tier T --out FILE``: the ``bun test`` arguments a tier command adds: a preload
  that arms the tier's guards in-process and journals the run, and Bun's JUnit reporter
  (``dialect``). ``report-check --dialect bun_test --exit-code <bun's exit code>`` then reads the
  report; the exit code is required.
- ``guard-evidence REPORT --tier T``: the report's journal shows the tier's guards were armed.
- ``affected --base REF [--head REF]``: the selector and T2 files this adapter runs.
- ``mutation-tool``: StrykerJS with its command test runner (``mutation``), T3 only.
- ``mutation-report --out FILE [--input FILE]``: Stryker's JSON as the shared mutation report.
- ``conformance [--case ID ...]``: the canonical cases over this adapter's fixtures.

Every ``--out`` path lies outside the checkout; guardrails never writes into the repository.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from guardrails.adapters import adapter_tiers, claimed
from guardrails.findings import Finding

NAME = "bun_test"
TIER_GUARDS = {
    "t1": ("fast-tier", "user-data"),
    "t2": ("user-data",),
    "t3": ("user-data",),
}
WRITING_ACTIONS = frozenset({"tiers", "junit-args", "mutation-report"})


def output_finding(repo: Path, out: object) -> Finding | None:
    target = Path(str(out)).expanduser()
    target = (target if target.is_absolute() else repo / target).resolve()
    if target.is_relative_to(repo.resolve()):
        return Finding(
            "GUARDRAILS.output-outside-repo",
            str(out),
            "adapter evidence must be outside the repository",
            "choose an external temporary directory",
        )
    return None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="guardrails adapter bun_test", description=__doc__
    )
    actions = root.add_subparsers(dest="action")
    lint = actions.add_parser("lint")
    lint.add_argument("paths", nargs="*")
    tiers = actions.add_parser("tiers")
    tiers.add_argument("--out", type=Path, required=True)
    tiers.add_argument(
        "--tier", choices=sorted(TIER_GUARDS), action="append", default=[]
    )
    junit = actions.add_parser("junit-args")
    junit.add_argument("--out", required=True)
    junit.add_argument("--tier", choices=sorted(TIER_GUARDS), required=True)
    evidence = actions.add_parser("guard-evidence")
    evidence.add_argument("report", type=Path)
    evidence.add_argument("--tier", choices=sorted(TIER_GUARDS), required=True)
    affected = actions.add_parser("affected")
    affected.add_argument("--base", required=True)
    affected.add_argument("--head", default="HEAD")
    actions.add_parser("mutation-tool")
    mutation = actions.add_parser("mutation-report")
    mutation.add_argument("--input", type=Path)
    mutation.add_argument("--out", type=Path, required=True)
    conformance = actions.add_parser("conformance")
    conformance.add_argument("--case", action="append", default=[])
    return root


def lint_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.bun_test.lint import lint

    return lint(repo, profile, options.paths)


def tiers_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.bun_test.tiers import list_tiers

    return list_tiers(repo, profile, options.tier, options.out)


def junit_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.bun_test.dialect import junit_arguments

    print(shlex.join(junit_arguments(options.tier, options.out)))
    return []


def evidence_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.bun_test.dialect import guard_evidence

    return guard_evidence(options.report, options.tier)


def affected_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.bun_test.cases import SUFFIXES
    from guardrails.adapters.bun_test.tiers import selector_arguments
    from guardrails.diffs.affected import RULE, affected
    from guardrails.diffs.changes import DiffError, range_finding

    try:
        selection = affected(
            repo, profile, ["--base", options.base, "--head", options.head]
        )
    except DiffError as error:
        return [range_finding(RULE, error)]
    print(f"selection: {selection.reason}", file=sys.stderr)
    paths = claimed(profile, NAME, "t2", selection.paths, SUFFIXES)
    if paths:
        selector = adapter_tiers(profile, NAME).get("t2", {}).get("selector", "")
        print(shlex.join([*selector_arguments(selector), *paths]))
    return []


def mutation_tool_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.bun_test.mutation import COMMAND, RUNNER, TOOL, VERSION

    print(
        f"{TOOL}@{VERSION} (testRunner {RUNNER!r}, commandRunner {COMMAND!r}, "
        "coverageAnalysis 'off'; tier t3; JSON reporter)"
    )
    return []


def mutation_report_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.bun_test.mutation import write_report
    from guardrails.structure.inventory import settings

    source = options.input or Path(
        settings(profile, "adapter_bun_test").get(
            "mutation_report", "reports/mutation/mutation.json"
        )
    )
    return write_report(repo, repo / source, options.out)


def conformance_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.bun_test.conformance import run_cases

    return run_cases(options.case, profile=profile)


Action = Callable[[Path, dict[str, Any], argparse.Namespace], list[Finding]]
ACTIONS: dict[str, Action] = {
    "lint": lint_action,
    "tiers": tiers_action,
    "junit-args": junit_action,
    "guard-evidence": evidence_action,
    "affected": affected_action,
    "mutation-tool": mutation_tool_action,
    "mutation-report": mutation_report_action,
}


def run(repo: Path, profile: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = parser().parse_args(argv or ["lint"])
    if options.action in WRITING_ACTIONS:
        problem = output_finding(repo, options.out)
        if problem:
            return [problem]
    return ACTIONS.get(options.action, conformance_action)(repo, profile, options)
