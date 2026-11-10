"""The pytest adapter: evidence and source patterns for the shared test checkers (plan §1.12).

``omp-guardrails adapter <action>`` with the profile's ``adapter`` set to ``pytest``:

- ``lint [PATH ...]`` (default): ast-grep rules for TEST.no-self-mocking, TEST.no-test-hooks,
  TEST.no-evasion (static skip, xfail and retry markers) and TEST.property-seed.
- ``tiers --out DIR [--tier T ...]``: collect-only test IDs per tier into ``DIR/<tier>.txt``.
- ``junit-args --tier T --out FILE``: prints the pytest arguments a tier command adds to emit a
  JUnit report with the run properties and arm the tier's guards. ``report-check`` then reads
  the report through this adapter's ``dialect.DIALECT`` (the shared schema's pytest dialect).
- ``guard-evidence REPORT --tier T``: the report's ``omp.guards`` property shows the tier's
  guards were armed (T1: fast-tier and user-data; T2 and T3: user-data) and ``omp.tier`` names
  the tier.
- ``mutation-tool``: prints the mutation tool for TEST.mutation-sensitivity. It runs only in T3
  (nightly) on changed code; the adapter never runs it in CI.
- ``conformance [--case ID ...]``: runs the framework-agnostic conformance cases.

``lint``, ``tiers`` and ``conformance`` need the ``pytest`` extra; the others are stdlib-only.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from omp_guardrails.findings import Finding

NAME = "pytest"
MUTATION_TOOL = "mutmut"
MUTATION_TIER = "t3"
PLUGIN = "omp_guardrails.adapters.pytest.plugin"
TIER_GUARDS = {"t1": ("fast-tier", "user-data"), "t2": ("user-data",), "t3": ("user-data",)}
GUARD_RULES = {"fast-tier": "TEST.fast-tier-isolation", "user-data": "TEST.user-data-guard"}
PROPAGATION = "TEST.failure-propagation"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="omp-guardrails adapter", description=__doc__)
    actions = root.add_subparsers(dest="action")
    lint = actions.add_parser("lint")
    lint.add_argument("paths", nargs="*")
    tiers = actions.add_parser("tiers")
    tiers.add_argument("--out", required=True, type=Path)
    tiers.add_argument("--tier", action="append", choices=sorted(TIER_GUARDS), default=[])
    junit_args = actions.add_parser("junit-args")
    junit_args.add_argument("--tier", required=True, choices=sorted(TIER_GUARDS))
    junit_args.add_argument("--out", required=True)
    evidence = actions.add_parser("guard-evidence")
    evidence.add_argument("report", type=Path)
    evidence.add_argument("--tier", required=True, choices=sorted(TIER_GUARDS))
    actions.add_parser("mutation-tool")
    conformance = actions.add_parser("conformance")
    conformance.add_argument("--case", action="append", default=[])
    return root


def tier_arguments(tier: str, report_path: str) -> list[str]:
    from omp_guardrails.adapters.pytest.dialect import junit_arguments

    guards = ",".join(TIER_GUARDS[tier])
    return [
        "-p",
        PLUGIN,
        f"--omp-guard={guards}",
        f"--omp-tier={tier}",
        *junit_arguments(report_path),
    ]


def report_finding(report: Path, error: Exception) -> Finding:
    return Finding(
        PROPAGATION,
        report.as_posix(),
        f"run report is missing or invalid: {error}",
        "rerun the tier with the adapter's junit-args; a missing report never passes",
    )


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    from omp_guardrails.adapters.pytest.dialect import DIALECT, GUARDS_PROPERTY
    from omp_guardrails.test_evidence.junit import ReportError, parse_report

    try:
        parsed = parse_report(report, DIALECT)
    except ReportError as error:
        return [report_finding(report, error)]
    recorded_tier = parsed.properties.get("omp.tier", "")
    if recorded_tier != tier:
        return [report_finding(report, ValueError(f"omp.tier is {recorded_tier!r}, not {tier!r}"))]
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


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    options = parser().parse_args(argv or ["lint"])
    action = options.action
    if action == "lint":
        from omp_guardrails.adapters.pytest.lint import lint

        return lint(repo, profile, options.paths)
    if action == "tiers":
        from omp_guardrails.adapters.pytest.tiers import list_tiers

        return list_tiers(repo, profile, options.tier, options.out)
    if action == "junit-args":
        print(shlex.join(tier_arguments(options.tier, options.out)))
        return []
    if action == "guard-evidence":
        return guard_evidence(options.report, options.tier)
    if action == "mutation-tool":
        print(f"{MUTATION_TOOL} (tier {MUTATION_TIER}, scoped to changed code; never run in CI)")
        return []
    from omp_guardrails.adapters.pytest.conformance import run_cases

    return run_cases(options.case)
