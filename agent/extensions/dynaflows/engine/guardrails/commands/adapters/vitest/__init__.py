from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from guardrails.foundation.findings import Finding
from guardrails.toolkit.dispatch import SUBCOMMANDS

NAME = "vitest"
TIER_GUARDS = {
    "t1": ("fast-tier", "user-data"),
    "t2": ("user-data",),
    "t3": ("user-data",),
}
WRITING_ACTIONS = frozenset({"tiers", "junit-args", "mutation-report"})


def output_finding(repo: Path, out: object) -> Finding | None:
    if Path(str(out)).resolve().is_relative_to(repo.resolve()):
        return Finding(
            "GUARDRAILS.output-outside-repo",
            str(out),
            "adapter evidence must be outside the repository",
            "choose an external temporary directory",
        )
    return None


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="guardrails adapter vitest", description=SUBCOMMANDS["adapter"].summary
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


def tier_arguments(tier: str, report_path: str) -> list[str]:
    from guardrails.commands.adapters.vitest.dialect import junit_arguments

    return junit_arguments(tier, report_path)


def affected_arguments(
    repo: Path, config: dict[str, Any], base: str, head: str
) -> list[Finding]:
    from guardrails.commands.adapters.affected import TierCommand, print_affected
    from guardrails.commands.adapters.vitest.cases import SUFFIXES
    from guardrails.commands.adapters.vitest.tiers import selector_arguments

    def t2_arguments(selector: object, paths: list[str]) -> list[str]:
        return [*selector_arguments(str(selector)), "--", *paths]

    command = TierCommand(NAME, SUFFIXES, t2_arguments)
    return print_affected(repo, config, command, base, head)


def guard_evidence(report: Path, tier: str) -> list[Finding]:
    from guardrails.commands.adapters.vitest.dialect import guard_evidence as check

    return check(report, tier)


def lint_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.vitest.lint import lint

    return lint(repo, config, options.paths)


def tiers_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.vitest.tiers import list_tiers

    return list_tiers(repo, config, options.tier, options.out)


def junit_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    print(shlex.join(tier_arguments(options.tier, options.out)))
    return []


def evidence_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    return guard_evidence(options.report, options.tier)


def affected_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    return affected_arguments(repo, config, options.base, options.head)


def mutation_tool_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.vitest.mutation import TOOL, VERSION

    print(
        f"{TOOL}@{VERSION} (tier t3; JSON reporter; run with the repository's locked toolchain)"
    )
    return []


def mutation_report_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.vitest.mutation import write_report
    from guardrails.foundation.inventory import settings

    source = options.input or Path(
        settings(config, "adapter_vitest").get(
            "mutation_report", "reports/mutation/mutation.json"
        )
    )
    return write_report(repo, repo / source, options.out)


def conformance_action(
    repo: Path, config: dict[str, Any], options: argparse.Namespace
) -> list[Finding]:
    from guardrails.commands.adapters.vitest.conformance import run_cases

    return run_cases(options.case, repo=repo, config=config)


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


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    options = parser().parse_args(argv or ["lint"])
    if options.action in WRITING_ACTIONS:
        problem = output_finding(repo, options.out)
        if problem:
            return [problem]
    return ACTIONS.get(options.action, conformance_action)(repo, config, options)
