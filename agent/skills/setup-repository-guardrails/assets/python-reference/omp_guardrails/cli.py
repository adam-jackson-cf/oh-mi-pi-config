"""Single entry point: ``omp-guardrails <subcommand> [--repo DIR] [args...]``.

Each subcommand names the checker modules it runs. Modules are imported only when their
subcommand runs, so a missing optional extra (the pytest adapter's) fails only that subcommand.
Every module exposes ``run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]``.
Exit codes: 0 no findings, 1 findings, 2 usage error.
``omp-guardrails <subcommand> --help`` prints the subcommand's module documentation without
reading the profile; ``init`` and ``adapter`` parse their own options and print their own help.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

from omp_guardrails.config.profile import PROFILE_PATH, ProfileError, load_profile
from omp_guardrails.findings import Finding, exit_code, format_findings


@dataclass(frozen=True)
class Subcommand:
    """The checker modules a subcommand runs, in order, and whether it needs a profile."""

    modules: tuple[str, ...]
    summary: str
    needs_profile: bool = True
    # Commands whose standard output is data (paths, arguments) print their status line on
    # standard error, so ``$(omp-guardrails ...)`` captures only the data.
    prints_data: bool = False


# The adapter subcommand resolves its module from the profile's ``adapter`` value.
ADAPTER_PACKAGE = "omp_guardrails.adapters"

SUBCOMMANDS: dict[str, Subcommand] = {
    "profile-check": Subcommand(
        ("omp_guardrails.config.profile",), "PROFILE.complete: validate .omp/test-profile.md"
    ),
    "init": Subcommand(
        ("omp_guardrails.init.command",),
        "write gate, hook, native configs and the pinned reference (greenfield/adopt/extend)",
        needs_profile=False,
    ),
    "path-check": Subcommand(
        ("omp_guardrails.structure.paths",), "GATE.path-check over changed or all paths"
    ),
    "structure": Subcommand(
        ("omp_guardrails.structure.layout",), "STRUCT.* repository layout allowlists"
    ),
    "naming": Subcommand(
        ("omp_guardrails.structure.naming",), "NAME.conventions and NAME.test-files"
    ),
    "python-quality": Subcommand(
        ("omp_guardrails.python_policy.quality",), "PYQ structure and class-shape rules"
    ),
    "python-fanout": Subcommand(
        ("omp_guardrails.python_policy.fanout",), "FANOUT.* import fan-out limits"
    ),
    "python-scalability": Subcommand(
        ("omp_guardrails.python_policy.scalability",), "PYS scalability rules"
    ),
    "python-test-hygiene": Subcommand(
        ("omp_guardrails.python_policy.subprocess_timeouts",),
        "TEST.subprocess-timeouts: bounded process calls in Python tests",
    ),
    "canaries": Subcommand(
        ("omp_guardrails.catalogue.canaries",), "rule canaries: planted violations must fail"
    ),
    "rule-evidence": Subcommand(
        ("omp_guardrails.catalogue.rule_evidence",), "rule evidence freshness"
    ),
    "hook-check": Subcommand(
        ("omp_guardrails.catalogue.hook_installation",), "GATE.pre-commit hook installation"
    ),
    "toolchain-parity": Subcommand(
        ("omp_guardrails.catalogue.toolchain_parity",), "PARITY.* toolchain parity"
    ),
    "gate": Subcommand(("omp_guardrails.catalogue.gate_runner",), "run the declared gate checks"),
    "report-check": Subcommand(
        (
            "omp_guardrails.test_evidence.budgets",
            "omp_guardrails.test_evidence.propagation",
        ),
        "TEST.budget.* and TEST.failure-propagation over the normalised run report",
    ),
    "collection-check": Subcommand(
        ("omp_guardrails.test_evidence.collection",), "TEST.tier-selectors over tier collections"
    ),
    "diff-check": Subcommand(
        (
            "omp_guardrails.diffs.coverage_ratchet",
            "omp_guardrails.diffs.golden_record",
            "omp_guardrails.diffs.identity_artefacts",
            "omp_guardrails.diffs.timeouts",
            "omp_guardrails.diffs.growth",
        ),
        "TEST.coverage-ratchet, TEST.golden-change-record, TEST.identity-artefacts, "
        "TEST.no-evasion timeout increases, TEST.growth.justified and TEST.duplicate-path",
    ),
    "affected": Subcommand(
        ("omp_guardrails.diffs.affected",),
        "affected T2 test files for a diff range, from the profile's Ownership areas",
        prints_data=True,
    ),
    "mutation-check": Subcommand(
        ("omp_guardrails.test_evidence.mutation",),
        "TEST.mutation-sensitivity over a normalised mutation report and the diff (T3)",
    ),
    "guard-run": Subcommand(
        ("omp_guardrails.environment.guarded_run",),
        "TEST.user-data-guard and TEST.fast-tier-isolation around a test command",
    ),
    "surface-lint": Subcommand(
        ("omp_guardrails.surface.feature_map", "omp_guardrails.surface.journeys"),
        "SURFACE.feature-map and SURFACE.journeys",
    ),
    "lever-selftest": Subcommand(
        ("omp_guardrails.surface.lever_selftest",), "SURFACE.lever-isolation"
    ),
    "adapter": Subcommand((), "run the profile's test-framework adapter", prints_data=True),
}


def usage() -> str:
    width = max(len(name) for name in SUBCOMMANDS)
    lines = ["usage: omp-guardrails <subcommand> [--repo DIR] [args...]", "", "subcommands:"]
    lines.extend(f"  {name.ljust(width)}  {item.summary}" for name, item in SUBCOMMANDS.items())
    return "\n".join(lines)


def wants_help(argv: list[str]) -> bool:
    """True when -h/--help appears before a ``--`` that starts a wrapped command."""
    options = argv[: argv.index("--")] if "--" in argv else argv
    return any(arg in {"-h", "--help"} for arg in options)


def subcommand_help(name: str) -> str:
    command = SUBCOMMANDS[name]
    lines = [f"usage: omp-guardrails {name} [--repo DIR] [args...]", "", command.summary]
    for module_name in command.modules:
        doc = inspect.getdoc(importlib.import_module(module_name)) or ""
        lines.extend(["", f"{module_name}:", doc])
    return "\n".join(lines)


def split_repo(argv: list[str]) -> tuple[Path, list[str]]:
    """Take a leading ``--repo DIR`` (default: the working directory) off the arguments."""
    if argv[:1] == ["--repo"] and len(argv) >= 2:
        return Path(argv[1]).resolve(), argv[2:]
    if argv[:1] and argv[0].startswith("--repo="):
        return Path(argv[0].split("=", 1)[1]).resolve(), argv[1:]
    return Path.cwd().resolve(), argv


def module_names(name: str, profile: dict) -> tuple[str, ...]:
    if name != "adapter":
        return SUBCOMMANDS[name].modules
    adapter = profile.get("adapter")
    if not isinstance(adapter, str) or not adapter.isidentifier():
        raise ProfileError("adapter must name an installed adapter package, e.g. 'pytest'")
    return (f"{ADAPTER_PACKAGE}.{adapter}",)


def run_subcommand(name: str, repo: Path, argv: list[str]) -> list[Finding]:
    command = SUBCOMMANDS[name]
    try:
        profile = load_profile(repo) if command.needs_profile else optional_profile(repo)
        modules = module_names(name, profile)
    except ProfileError as error:
        return [profile_finding(error)]
    findings: list[Finding] = []
    for module_name in modules:
        module = importlib.import_module(module_name)
        findings.extend(module.run(repo, profile, argv))
    return findings


def optional_profile(repo: Path) -> dict:
    """Commands that create the profile (init) run before it exists."""
    try:
        return load_profile(repo)
    except ProfileError:
        return {}


def profile_finding(error: ProfileError) -> Finding:
    return Finding(
        "PROFILE.complete",
        PROFILE_PATH,
        str(error),
        "write the machine-readable front matter from the test-profile template",
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0 if args else 2
    name, rest = args[0], args[1:]
    if name not in SUBCOMMANDS:
        print(f"unknown subcommand: {name}\n\n{usage()}", file=sys.stderr)
        return 2
    if wants_help(rest) and SUBCOMMANDS[name].needs_profile and name != "adapter":
        print(subcommand_help(name))
        return 0
    repo, rest = split_repo(rest)
    try:
        findings = run_subcommand(name, repo, rest)
    except ModuleNotFoundError as error:
        print(
            f"{name}: cannot import {error.name}; install the extra this subcommand needs "
            "(e.g. omp-guardrails[pytest])",
            file=sys.stderr,
        )
        return 2
    if findings:
        print(format_findings(findings))
    else:
        print(f"{name}: ok", file=sys.stderr if SUBCOMMANDS[name].prints_data else sys.stdout)
    return exit_code(findings)


if __name__ == "__main__":
    raise SystemExit(main())
