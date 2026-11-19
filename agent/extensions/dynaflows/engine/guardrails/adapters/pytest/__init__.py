"""The pytest adapter: evidence and source patterns for the shared test checkers (plan §1.12).

``guardrails adapter pytest <action>`` when the profile's ``adapters`` include ``pytest``:

- ``lint [PATH ...]`` (default): ast-grep rules for TEST.no-self-mocking, TEST.no-test-hooks,
  TEST.no-evasion (static skip, xfail and retry markers) and TEST.property-seed.
- ``tiers --out DIR [--tier T ...]``: collect-only test IDs per tier into ``DIR/<tier>.txt``.
- ``junit-args --tier T --out FILE``: prints the pytest arguments a tier command adds to emit a
  JUnit report with the run properties and arm the tier's guards. ``report-check`` (commit and
  CI gates) and ``budget-check`` (local commit gate only) then read the report through this
  adapter's ``dialect.DIALECT`` (the shared schema's pytest dialect).
- ``guard-evidence REPORT --tier T``: the report's ``omp.guards`` property shows the tier's
  guards were armed (T1: fast-tier and user-data; T2 and T3: user-data) and ``omp.tier`` names
  the tier.
- ``affected --base REF [--head REF]``: the pytest arguments that run the affected T2 tests
  (shared selection in ``guardrails.diffs.affected``, narrowed to the Python files this adapter
  runs); prints nothing when none are affected, and the reason on standard error.
- ``mutation-tool``: prints the mutation tool for TEST.mutation-sensitivity (mutmut, pinned by
  the ``mutation`` extra). It runs only in T3 (nightly); the adapter never runs it in CI.
- ``mutation-report --out FILE``: after ``mutmut run``, writes mutmut's results as the shared
  mutation report that ``guardrails mutation-check`` reads.
- ``conformance [--case ID ...]``: runs the framework-agnostic conformance cases.

``lint``, ``tiers`` and ``conformance`` need the ``pytest`` extra; the others are stdlib-only.
guardrails never writes into the repository tree, so every ``--out`` path (the tier listing,
the JUnit report, the mutation report) must lie outside the checkout.
"""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from guardrails.adapters import adapter_tiers, claimed
from guardrails.findings import Finding

NAME = "pytest"
SUFFIXES = (".py",)
MUTATION_TOOL = "mutmut"
MUTATION_TIER = "t3"
PLUGIN = "guardrails.adapters.pytest.plugin"
TIER_GUARDS = {
    "t1": ("fast-tier", "user-data"),
    "t2": ("user-data",),
    "t3": ("user-data",),
}
GUARD_RULES = {
    "fast-tier": "TEST.fast-tier-isolation",
    "user-data": "TEST.user-data-guard",
}
PROPAGATION = "TEST.failure-propagation"
OUTPUT_RULE = "GUARDRAILS.output-outside-repo"
WRITING_ACTIONS = frozenset({"tiers", "junit-args", "mutation-report"})


def output_finding(repo: Path, out: object) -> Finding | None:
    """A finding when ``out`` would put an output file inside the repository.

    A relative path counts as repository-relative, so outputs always name an absolute path.
    """
    target = Path(str(out)).expanduser()
    target = (target if target.is_absolute() else repo / target).resolve()
    if target != repo.resolve() and not target.is_relative_to(repo.resolve()):
        return None
    return Finding(
        OUTPUT_RULE,
        str(out),
        "output path is inside the repository; guardrails never writes into the repo tree",
        "pass an --out path outside the checkout, e.g. under the system temporary directory",
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="guardrails adapter pytest", description=__doc__
    )
    actions = root.add_subparsers(dest="action")
    lint = actions.add_parser("lint")
    lint.add_argument("paths", nargs="*")
    tiers = actions.add_parser("tiers")
    tiers.add_argument("--out", required=True, type=Path)
    tiers.add_argument(
        "--tier", action="append", choices=sorted(TIER_GUARDS), default=[]
    )
    junit_args = actions.add_parser("junit-args")
    junit_args.add_argument("--tier", required=True, choices=sorted(TIER_GUARDS))
    junit_args.add_argument("--out", required=True)
    evidence = actions.add_parser("guard-evidence")
    evidence.add_argument("report", type=Path)
    evidence.add_argument("--tier", required=True, choices=sorted(TIER_GUARDS))
    affected = actions.add_parser("affected")
    affected.add_argument("--base", required=True)
    affected.add_argument("--head", default="HEAD")
    actions.add_parser("mutation-tool")
    mutation_report = actions.add_parser("mutation-report")
    mutation_report.add_argument("--out", required=True, type=Path)
    conformance = actions.add_parser("conformance")
    conformance.add_argument("--case", action="append", default=[])
    return root


def tier_arguments(tier: str, report_path: str) -> list[str]:
    from guardrails.adapters.pytest.dialect import junit_arguments

    guards = ",".join(TIER_GUARDS[tier])
    return [
        "-p",
        PLUGIN,
        f"--omp-guard={guards}",
        f"--omp-tier={tier}",
        *junit_arguments(report_path),
    ]


def affected_arguments(
    repo: Path, profile: dict[str, Any], base: str, head: str
) -> list[Finding]:
    """Print the affected T2 files with the T2 marker selector as pytest arguments."""
    import sys

    from guardrails.diffs.affected import RULE, affected
    from guardrails.diffs.changes import DiffError, range_finding

    try:
        selection = affected(repo, profile, ["--base", base, "--head", head])
    except DiffError as error:
        return [range_finding(RULE, error)]
    print(f"selection: {selection.reason}", file=sys.stderr)
    paths = claimed(profile, NAME, "t2", selection.paths, SUFFIXES)
    if paths:
        from guardrails.adapters.pytest.tiers import selector_arguments

        selector = adapter_tiers(profile, NAME).get("t2", {}).get("selector")
        chosen = (
            selector_arguments(selector)
            if isinstance(selector, str) and selector
            else []
        )
        print(shlex.join([*chosen, "--", *paths]))
    return []


def report_finding(report: Path, error: Exception) -> Finding:
    return Finding(
        PROPAGATION,
        report.as_posix(),
        f"run report is missing or invalid: {error}",
        "rerun the tier with the adapter's junit-args; a missing report never passes",
    )


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    from guardrails.adapters.pytest.dialect import DIALECT, GUARDS_PROPERTY
    from guardrails.test_evidence.junit import ReportError, parse_report

    try:
        parsed = parse_report(report, DIALECT)
    except ReportError as error:
        return [report_finding(report, error)]
    recorded_tier = parsed.properties.get("omp.tier", "")
    if recorded_tier != tier:
        return [
            report_finding(
                report, ValueError(f"omp.tier is {recorded_tier!r}, not {tier!r}")
            )
        ]
    armed = set(filter(None, parsed.properties.get(GUARDS_PROPERTY, "").split(",")))
    return [
        Finding(
            GUARD_RULES[guard],
            report.as_posix(),
            f"tier {tier} ran without the {guard} guard",
            f"run tier {tier} with: {shlex.join(tier_arguments(tier, '<report>'))}",
        )
        for guard in TIER_GUARDS[tier]
        if guard not in armed
    ]


def lint_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.pytest.lint import lint

    return lint(repo, profile, options.paths)


def tiers_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.pytest.tiers import list_tiers

    return list_tiers(repo, profile, options.tier, options.out)


def junit_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    print(shlex.join(tier_arguments(options.tier, options.out)))
    return []


def evidence_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    return guard_evidence(options.report, options.tier)


def affected_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    return affected_arguments(repo, profile, options.base, options.head)


def mutation_tool_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    print(
        f"{MUTATION_TOOL} (tier {MUTATION_TIER}, scoped to changed code; never run in CI)"
    )
    return []


def mutation_report_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.pytest.mutation import write_report

    return write_report(repo, options.out)


def conformance_action(
    repo: Path, profile: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.adapters.pytest.conformance import run_cases

    return run_cases(options.case)


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
