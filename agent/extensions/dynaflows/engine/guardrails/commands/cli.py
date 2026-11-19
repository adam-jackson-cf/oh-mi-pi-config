"""Single entry point: ``guardrails <subcommand> [--repo DIR] [--record DIR] [args...]``.

guardrails runs from the harness, never from the repository's environment: the agent rails
call ``uv run --project <omp root>/agent/extensions/dynaflows/engine
guardrails check --repo <worktree> --record <record dir>``. ``--repo`` is the
checkout to inspect (default: the working directory). ``--record`` is the repo's personal
per-repo record (``<oh-mi-pi-config root>/repos/<owner>/<name>/``); its ``profile.md`` is the
only configuration. A record inside the repository is refused: the repository holds nothing of
the harness. guardrails never writes into the repository tree; output files go outside it.

Each subcommand names the checker modules it runs. Modules are imported only when their
subcommand runs, so a missing optional extra (the pytest adapter's) fails only that subcommand.
Every module exposes ``run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]``.
Exit codes: 0 no findings, 1 findings, 2 usage error (including a missing ``--record``).
``guardrails <subcommand> --help`` prints the subcommand's module documentation without
reading the profile; ``init`` and ``adapter`` parse their own options and print their own help.
``adapter`` takes the adapter's name first (``guardrails adapter pytest tiers --out DIR``): a
profile may declare several frameworks, so the caller always names one.
``init`` drafts the record itself, so it is the one subcommand that writes: directly into a live
record or a draft directory. ``--enable-dynaflows`` drafts dynaflows on.
"""

from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

from guardrails.context.config.profile import ProfileError, load_profile
from guardrails.foundation.findings import Finding, exit_code, format_findings
from guardrails.toolkit.dispatch import (
    SUBCOMMANDS,
    adapter_module,
    checker_module,
    module_names,
    profile_finding,
    run_modules,
)


def usage() -> str:
    width = max(len(name) for name in SUBCOMMANDS)
    lines = [
        "usage: guardrails <subcommand> [--repo DIR] [--record DIR] [args...]",
        "",
        "subcommands:",
    ]
    lines.extend(
        f"  {name.ljust(width)}  {item.summary}" for name, item in SUBCOMMANDS.items()
    )
    return "\n".join(lines)


def wants_help(argv: list[str]) -> bool:
    """True when -h/--help appears before a ``--`` that starts a wrapped command."""
    options = argv[: argv.index("--")] if "--" in argv else argv
    return any(arg in {"-h", "--help"} for arg in options)


def subcommand_help(name: str) -> str:
    command = SUBCOMMANDS[name]
    lines = [
        f"usage: guardrails {name} [--repo DIR] [--record DIR] [args...]",
        "",
        command.summary,
    ]
    for module_name in command.modules:
        doc = inspect.getdoc(checker_module(module_name)) or ""
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
        raise UsageError(
            f"--record {record} is inside the repository; records live in the harness"
        )
    return Target(repo, record), rest


def run_subcommand(name: str, target: Target, argv: list[str]) -> list[Finding]:
    if name == "init":
        assert target.record is not None
        init_command = checker_module(SUBCOMMANDS[name].modules[0])
        drafted: list[Finding] = init_command.run_init(target.repo, target.record, argv)
        return drafted
    if name == "structure-state" and target.record is None:
        return run_modules(module_names(name, {}), target.repo, {}, argv)
    assert target.record is not None
    try:
        profile = load_profile(target.record)
        if name == "adapter":
            module, argv = adapter_module(profile, argv)
            modules: tuple[str, ...] = (module,)
        else:
            modules = module_names(name, profile)
    except ProfileError as error:
        return [profile_finding(error)]
    return run_modules(modules, target.repo, profile, argv)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage())
        return 0 if args else 2
    name, rest = args[0], args[1:]
    if name not in SUBCOMMANDS:
        print(f"unknown subcommand: {name}\n\n{usage()}", file=sys.stderr)
        return 2
    if (
        wants_help(rest)
        and name != "adapter"
        and (SUBCOMMANDS[name].needs_profile or name == "structure-state")
    ):
        print(subcommand_help(name))
        return 0
    try:
        target, rest = resolve_target(rest, needs_record=name != "structure-state")
    except UsageError as error:
        print(f"{name}: {error}", file=sys.stderr)
        return 2
    try:
        findings = run_subcommand(name, target, rest)
    except ModuleNotFoundError as error:
        print(
            f"{name}: cannot import {error.name}; run guardrails with the extra this "
            "subcommand needs (uv run --project <guardrails> --extra pytest ...)",
            file=sys.stderr,
        )
        return 2
    if findings:
        print(format_findings(findings))
    else:
        print(
            f"{name}: ok",
            file=sys.stderr if SUBCOMMANDS[name].prints_data else sys.stdout,
        )
    return exit_code(findings)


if __name__ == "__main__":
    raise SystemExit(main())
