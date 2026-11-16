"""Harness-side Vitest adapter; repository selectors and commands live in its record."""
from __future__ import annotations

import argparse
import shlex
import sys
from pathlib import Path

from omp_guardrails.findings import Finding

NAME = "vitest"
TIER_GUARDS = {"t1": ("fast-tier", "user-data"), "t2": ("user-data",), "t3": ("user-data",)}
WRITING_ACTIONS = frozenset({"tiers", "junit-args", "mutation-report"})


def output_finding(repo: Path, out: object) -> Finding | None:
    if Path(str(out)).resolve().is_relative_to(repo.resolve()):
        return Finding("GUARDRAILS.output-outside-repo", str(out), "adapter evidence must be outside the repository", "choose an external temporary directory")
    return None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="omp-guardrails adapter", description=__doc__)
    actions = root.add_subparsers(dest="action")
    lint = actions.add_parser("lint")
    lint.add_argument("paths", nargs="*")
    tiers = actions.add_parser("tiers")
    tiers.add_argument("--out", type=Path, required=True)
    tiers.add_argument("--tier", choices=sorted(TIER_GUARDS), action="append", default=[])
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


def tier_arguments(tier: str, report_path: str) -> list[str]:
    from omp_guardrails.adapters.vitest.dialect import junit_arguments

    return junit_arguments(tier, report_path)


def affected_arguments(repo: Path, profile: dict, base: str, head: str) -> list[Finding]:
    from omp_guardrails.adapters.vitest.tiers import selector_arguments
    from omp_guardrails.diffs.affected import RULE, affected
    from omp_guardrails.diffs.changes import DiffError, range_finding

    try:
        selection = affected(repo, profile, ["--base", base, "--head", head])
    except DiffError as error:
        return [range_finding(RULE, error)]
    print(f"selection: {selection.reason}", file=sys.stderr)
    if selection.paths:
        selector = profile.get("tiers", {}).get("t2", {}).get("selector", "")
        print(shlex.join([*selector_arguments(selector), "--", *selection.paths]))
    return []


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    from omp_guardrails.adapters.vitest.dialect import guard_evidence as check

    return check(report, tier)


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    options = parser().parse_args(argv or ["lint"])
    if options.action in WRITING_ACTIONS:
        problem = output_finding(repo, options.out)
        if problem:
            return [problem]
    if options.action == "lint":
        from omp_guardrails.adapters.vitest.lint import lint

        return lint(repo, profile, options.paths)
    if options.action == "tiers":
        from omp_guardrails.adapters.vitest.tiers import list_tiers

        return list_tiers(repo, profile, options.tier, options.out)
    if options.action == "junit-args":
        print(shlex.join(tier_arguments(options.tier, options.out)))
        return []
    if options.action == "guard-evidence":
        return guard_evidence(options.report, options.tier)
    if options.action == "affected":
        return affected_arguments(repo, profile, options.base, options.head)
    if options.action == "mutation-tool":
        from omp_guardrails.adapters.vitest.mutation import TOOL, VERSION

        print(f"{TOOL}@{VERSION} (tier t3; JSON reporter; run with the repository's locked toolchain)")
        return []
    if options.action == "mutation-report":
        from omp_guardrails.adapters.vitest.mutation import write_report
        from omp_guardrails.structure.inventory import settings

        source = options.input or Path(settings(profile, "adapter_vitest").get("mutation_report", "reports/mutation/mutation.json"))
        return write_report(repo, repo / source, options.out)
    from omp_guardrails.adapters.vitest.conformance import run_cases

    return run_cases(options.case, repo=repo, profile=profile)
