"""Single entry point: ``omp-guardrails <subcommand> [--repo DIR] [--record DIR] [args...]``.

omp-guardrails runs from the harness, never from the repository's environment: the agent rails
call ``uv run --project <omp root>/agent/skills/setup-repository-guardrails/assets/
python-reference omp-guardrails check --repo <worktree> --record <record dir>``. ``--repo`` is the
checkout to inspect (default: the working directory). ``--record`` is the repo's personal
per-repo record (``<oh-mi-pi-config root>/repos/<owner>/<name>/``); its ``profile.md`` is the
only configuration. A record inside the repository is refused: the repository holds nothing of
the harness. omp-guardrails never writes into the repository tree; output files go outside it.

Each subcommand names the checker modules it runs. Modules are imported only when their
subcommand runs, so a missing optional extra (the pytest adapter's) fails only that subcommand.
Every module exposes ``run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]``.
Exit codes: 0 no findings, 1 findings, 2 usage error (including a missing ``--record``).
``omp-guardrails <subcommand> --help`` prints the subcommand's module documentation without
reading the profile; ``init`` and ``adapter`` parse their own options and print their own help.
``init`` drafts the record itself, so it is the one subcommand that writes (into the record).
"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

from omp_guardrails.config.profile import PROFILE_FILE, PROFILE_PATH, ProfileError, load_profile
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
    "check": Subcommand(
        ("omp_guardrails.whole_repo",),
        "the whole-repo harness checks the agent rails run at commit (any finding blocks)",
    ),
    "profile-check": Subcommand(
        ("omp_guardrails.config.profile",), "PROFILE.complete: validate the record's profile.md"
    ),
    "init": Subcommand(
        ("omp_guardrails.init.command",),
        "draft the repo's local record (gate.json, profile.md) for owner confirmation",
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
    "toolchain-parity": Subcommand(
        ("omp_guardrails.catalogue.toolchain_parity",), "PARITY.* toolchain parity"
    ),
    "report-check": Subcommand(
        ("omp_guardrails.test_evidence.propagation",),
        "TEST.failure-propagation over the normalised run report (commit and CI gates)",
    ),
    "budget-check": Subcommand(
        ("omp_guardrails.test_evidence.budgets",),
        "TEST.budget.* over the fast-tier duration report (local commit gate only)",
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
    lines = [
        "usage: omp-guardrails <subcommand> [--repo DIR] [--record DIR] [args...]",
        "",
        "subcommands:",
    ]
    lines.extend(f"  {name.ljust(width)}  {item.summary}" for name, item in SUBCOMMANDS.items())
    return "\n".join(lines)


def wants_help(argv: list[str]) -> bool:
    """True when -h/--help appears before a ``--`` that starts a wrapped command."""
    options = argv[: argv.index("--")] if "--" in argv else argv
    return any(arg in {"-h", "--help"} for arg in options)


def subcommand_help(name: str) -> str:
    command = SUBCOMMANDS[name]
    lines = [
        f"usage: omp-guardrails {name} [--repo DIR] [--record DIR] [args...]",
        "",
        command.summary,
    ]
    for module_name in command.modules:
        doc = inspect.getdoc(importlib.import_module(module_name)) or ""
        lines.extend(["", f"{module_name}:", doc])
    return "\n".join(lines)


class UsageError(Exception):
    """The command line names no usable repository or record."""


@dataclass(frozen=True)
class Target:
    """The checkout a subcommand inspects and the record that configures it."""

    repo: Path
    record: Path | None


LOCATION_OPTIONS = ("--repo", "--record")


def split_locations(argv: list[str]) -> tuple[dict[str, Path], list[str]]:
    """Take leading ``--repo DIR`` and ``--record DIR`` options (either order) off the arguments."""
    found: dict[str, Path] = {}
    rest = list(argv)
    while rest:
        option, _, value = rest[0].partition("=")
        if option not in LOCATION_OPTIONS or option in found:
            break
        if not value:
            if len(rest) < 2:
                raise UsageError(f"{option} needs a directory")
            value, rest = rest[1], rest[2:]
        else:
            rest = rest[1:]
        found[option] = Path(value).expanduser().resolve()
    return found, rest


def resolve_target(argv: list[str], needs_record: bool) -> tuple[Target, list[str]]:
    found, rest = split_locations(argv)
    repo = found.get("--repo", Path.cwd().resolve())
    record = found.get("--record")
    if record is None:
        if needs_record:
            raise UsageError(
                "pass --record DIR: the repo's record under <oh-mi-pi-config>/repos/<owner>/<name>"
            )
        return Target(repo, None), rest
    if record == repo or record.is_relative_to(repo):
        raise UsageError(f"--record {record} is inside the repository; records live in the harness")
    return Target(repo, record), rest


def module_names(name: str, profile: dict) -> tuple[str, ...]:
    if name != "adapter":
        return SUBCOMMANDS[name].modules
    adapter = profile.get("adapter")
    if not isinstance(adapter, str) or not adapter.isidentifier():
        raise ProfileError("adapter must name an installed adapter package, e.g. 'pytest'")
    return (f"{ADAPTER_PACKAGE}.{adapter}",)


def run_subcommand(name: str, target: Target, argv: list[str]) -> list[Finding]:
    if name == "init":
        from omp_guardrails.init.command import run_init

        assert target.record is not None
        return run_init(target.repo, target.record, argv)
    assert target.record is not None
    try:
        profile = load_profile(target.record)
        modules = module_names(name, profile)
    except ProfileError as error:
        return [profile_finding(error)]
    return run_modules(modules, target.repo, profile, argv)


def run_modules(
    modules: tuple[str, ...], repo: Path, profile: dict, argv: list[str]
) -> list[Finding]:
    findings: list[Finding] = []
    for module_name in modules:
        module = importlib.import_module(module_name)
        findings.extend(module.run(repo, profile, argv))
    return findings


def profile_finding(error: ProfileError) -> Finding:
    return Finding(
        "PROFILE.complete",
        PROFILE_PATH,
        str(error),
        f"write the record's {PROFILE_FILE} front matter from the test-profile template "
        "(omp-guardrails init drafts it)",
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
    try:
        target, rest = resolve_target(rest, needs_record=True)
    except UsageError as error:
        print(f"{name}: {error}", file=sys.stderr)
        return 2
    try:
        findings = run_subcommand(name, target, rest)
    except ModuleNotFoundError as error:
        print(
            f"{name}: cannot import {error.name}; run omp-guardrails with the extra this "
            "subcommand needs (uv run --project <python-reference> --extra pytest ...)",
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
