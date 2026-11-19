"""Harness-side Vitest adapter; repository selectors and commands live in its record."""
from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from guardrails.findings import Finding

NAME = "vitest"
TIER_GUARDS = {"t1": ("fast-tier", "user-data"), "t2": ("user-data",), "t3": ("user-data",)}
WRITING_ACTIONS = frozenset({"tiers", "junit-args", "mutation-report"})


def output_finding(repo: Path, out: object) -> Finding | None:
    if Path(str(out)).resolve().is_relative_to(repo.resolve()):
        return Finding("GUARDRAILS.output-outside-repo", str(out), "adapter evidence must be outside the repository", "choose an external temporary directory")
    return None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="guardrails adapter", description=__doc__)
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
    from guardrails.adapters.vitest.dialect import junit_arguments

    return junit_arguments(tier, report_path)


def affected_arguments(repo: Path, profile: dict[str, Any], base: str, head: str) -> list[Finding]:
    from guardrails.adapters.vitest.tiers import selector_arguments
    from guardrails.diffs.affected import RULE, affected
    from guardrails.diffs.changes import DiffError, range_finding

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
    from guardrails.adapters.vitest.dialect import guard_evidence as check

    return check(report, tier)


def lint_action(repo: Path, profile: dict[str, Any], options: argparse.Namespace) -> list[Finding]:
    from guardrails.adapters.vitest.lint import lint

    return lint(repo, profile, options.paths)


def tiers_action(repo: Path, profile: dict[str, Any], options: argparse.Namespace) -> list[Finding]:
    from guardrails.adapters.vitest.tiers import list_tiers

    return list_tiers(repo, profile, options.tier, options.out)


def junit_action(repo: Path, profile: dict[str, Any], options: argparse.Namespace) -> list[Finding]:
    print(shlex.join(tier_arguments(options.tier, options.out)))
    return []


def evidence_action(repo: Path, profile: dict[str, Any], options: argparse.Namespace) -> list[Finding]:
    return guard_evidence(options.report, options.tier)


def affected_action(repo: Path, profile: dict[str, Any], options: argparse.Namespace) -> list[Finding]:
    return affected_arguments(repo, profile, options.base, options.head)


def mutation_tool_action(repo: Path, profile: dict[str, Any], options: argparse.Namespace) -> list[Finding]:
    from guardrails.adapters.vitest.mutation import TOOL, VERSION

    print(f"{TOOL}@{VERSION} (tier t3; JSON reporter; run with the repository's locked toolchain)")
    return []


def mutation_report_action(repo: Path, profile: dict[str, Any], options: argparse.Namespace) -> list[Finding]:
    from guardrails.adapters.vitest.mutation import write_report
    from guardrails.structure.inventory import settings

    source = options.input or Path(settings(profile, "adapter_vitest").get("mutation_report", "reports/mutation/mutation.json"))
    return write_report(repo, repo / source, options.out)


def conformance_action(repo: Path, profile: dict[str, Any], options: argparse.Namespace) -> list[Finding]:
    from guardrails.adapters.vitest.conformance import run_cases

    return run_cases(options.case, repo=repo, profile=profile)


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
