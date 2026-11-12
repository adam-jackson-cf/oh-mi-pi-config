"""The pytest adapter: evidence and source patterns for the shared test checkers (plan §1.12).

``omp-guardrails adapter <action>`` with the profile's ``adapter`` set to ``pytest``:

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
  (shared selection in ``omp_guardrails.diffs.affected``); prints nothing when none are
  affected, and the reason on standard error.
- ``mutation-tool``: prints the mutation tool for TEST.mutation-sensitivity (mutmut, pinned by
  the ``mutation`` extra). It runs only in T3 (nightly); the adapter never runs it in CI.
- ``mutation-report --out FILE``: after ``mutmut run``, writes mutmut's results as the shared
  mutation report that ``omp-guardrails mutation-check`` reads.
- ``conformance [--case ID ...]``: runs the framework-agnostic conformance cases.

``lint``, ``tiers`` and ``conformance`` need the ``pytest`` extra; the others are stdlib-only.
omp-guardrails never writes into the repository tree, so every ``--out`` path (the tier listing,
the JUnit report, the mutation report) must lie outside the checkout.
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
        "output path is inside the repository; omp-guardrails never writes into the repo tree",
        "pass an --out path outside the checkout, e.g. under the system temporary directory",
    )


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
    from omp_guardrails.adapters.pytest.dialect import junit_arguments

    guards = ",".join(TIER_GUARDS[tier])
    return [
        "-p",
        PLUGIN,
        f"--omp-guard={guards}",
        f"--omp-tier={tier}",
        *junit_arguments(report_path),
    ]


def affected_arguments(repo: Path, profile: dict, base: str, head: str) -> list[Finding]:
    """Print the affected T2 files with the T2 marker selector as pytest arguments."""
    import sys

    from omp_guardrails.diffs.affected import RULE, affected
    from omp_guardrails.diffs.changes import DiffError, range_finding

    try:
        selection = affected(repo, profile, ["--base", base, "--head", head])
    except DiffError as error:
        return [range_finding(RULE, error)]
    print(f"selection: {selection.reason}", file=sys.stderr)
    if selection.paths:
        from omp_guardrails.adapters.pytest.tiers import selector_arguments

        tiers = profile.get("tiers") if isinstance(profile.get("tiers"), dict) else {}
        t2 = tiers.get("t2") if isinstance(tiers.get("t2"), dict) else {}
        selector = t2.get("selector")
        chosen = selector_arguments(selector) if isinstance(selector, str) and selector else []
        print(shlex.join([*chosen, "--", *selection.paths]))
    return []


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
    if action in WRITING_ACTIONS:
        problem = output_finding(repo, options.out)
        if problem:
            return [problem]
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
    if action == "affected":
        return affected_arguments(repo, profile, options.base, options.head)
    if action == "mutation-tool":
        print(f"{MUTATION_TOOL} (tier {MUTATION_TIER}, scoped to changed code; never run in CI)")
        return []
    if action == "mutation-report":
        from omp_guardrails.adapters.pytest.mutation import write_report

        return write_report(repo, options.out)
    from omp_guardrails.adapters.pytest.conformance import run_cases

    return run_cases(options.case)
