"""guardrails init: draft the setup-owned config sections into a file outside the repo.

The draft holds commands, checkers, structure, houseStyle and tests. It never writes into
the repository or its .agentrails/ config; the agentrails_config writer applies sections.
"""

from __future__ import annotations

import argparse
import contextlib
import shlex
from pathlib import Path
from typing import Any

from guardrails.commands.init import (
    commands_draft,
    discovery,
    lsp,
    native_languages,
    native_python,
    sections_draft,
)
from guardrails.context.config.section_schema import ConfigValueError
from guardrails.context.config.validator import (
    ConfigError,
    load_config,
    main_checkout,
)
from guardrails.foundation.findings import Finding, config_label
from guardrails.subcommand.dispatch import SUBCOMMANDS

USAGE_RULE = "CONFIG.complete"


class UsageError(Exception):
    pass


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="guardrails init", description=SUBCOMMANDS["init"].summary
    )
    command.add_argument(
        "--out",
        type=Path,
        required=True,
        help="the draft file to write; it must be outside the repository",
    )
    command.add_argument("--commit-command", help="the repo's own commit gate command")
    command.add_argument("--ci-command", help="the repo's own CI gate command")
    command.add_argument(
        "--settings", type=Path, help="JSON object of checkers settings"
    )
    command.add_argument(
        "--offline", action="store_true", help="do not ask gh for the required checks"
    )
    command.add_argument("--allow-missing-lsp", action="store_true")
    command.add_argument(
        "--lsp-timeout",
        type=float,
        default=lsp.DEFAULT_TIMEOUT_S,
        help="seconds each language-server canary may take",
    )
    return command


def parse(argv: list[str]) -> argparse.Namespace:
    try:
        options = parser().parse_args(argv)
    except SystemExit as error:
        raise UsageError(f"invalid init arguments: {shlex.join(argv)}") from error
    for flag in ("commit_command", "ci_command"):
        value = getattr(options, flag)
        if value is not None and not value.strip():
            raise UsageError(f"--{flag.replace('_', '-')} must not be empty")
    return options


def repository_roots(repo: Path) -> list[Path]:
    roots = [repo.resolve()]
    with contextlib.suppress(ConfigError):
        roots.append(main_checkout(repo).resolve())
    return roots


def draft_path(repo: Path, out: Path) -> Path:
    path = out.expanduser().resolve()
    for root in repository_roots(repo):
        if path == root or path.is_relative_to(root):
            raise UsageError(
                f"--out {path} is inside the repository {root}; write the draft outside it"
            )
    return path


def existing_sections(repo: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        config = load_config(repo)
    except ConfigError as error:
        return {}, [f"init: drafting from the section template (config {error.kind})"]
    sections = {
        name: config[name] for name in sections_draft.SETUP_SECTIONS if name in config
    }
    return sections, ["init: drafting from the repo's .agentrails/config.json"]


def write_if_changed(path: Path, text: str, changes: list[str]) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    changes.append(f"wrote {path}")


def native_proposals(repo: Path, structure: object) -> list[str]:
    """Native tool configs the repo lacks, printed for the agent to apply as a fix."""
    planned, report = native_languages.planned_configs(repo)
    if (repo / "pyproject.toml").is_file():
        python, lines = native_python.planned_configs(repo, {"structure": structure})
        planned.update(python)
        report = lines + report
    for name, text in planned.items():
        report += [
            f"native: proposed {name} (apply it to the repository as a fix)",
            f"--- begin {name}",
            *text.splitlines(),
            f"--- end {name}",
        ]
    return report


def initialise(
    repo: Path, out: Path, options: argparse.Namespace
) -> tuple[list[str], list[Finding]]:
    found = discovery.discover(repo, offline=options.offline)
    base, origin = existing_sections(repo)
    settings = sections_draft.load_settings(options.settings)
    notes = lsp.notes(lsp.detect(repo))
    sections, report = sections_draft.drafted_sections(base, settings, notes)
    sections["commands"], confirmations = commands_draft.planned_commands(
        sections["commands"], found, options.commit_command, options.ci_command
    )
    changes: list[str] = []
    write_if_changed(out, sections_draft.render(sections), changes)
    report = found.report() + origin + report + confirmations
    report += [
        f"fill: {problem}" for problem in sections_draft.section_problems(sections)
    ]
    report += native_proposals(repo, sections["structure"])
    lsp_findings, lsp_lines = lsp.readiness(
        repo,
        lsp.detect(repo),
        allow_missing=options.allow_missing_lsp,
        timeout=options.lsp_timeout,
    )
    summary = [f"init: {line}" for line in changes or ["no changes"]]
    handoff = [f"init: draft at {out}."]
    return summary + report + lsp_lines + handoff, lsp_findings


def usage_finding(message: str) -> Finding:
    return Finding(USAGE_RULE, "guardrails init", message, "fix the init arguments")


def run_init(repo: Path, argv: list[str]) -> list[Finding]:
    if {"-h", "--help"} & set(argv):
        parser().print_help()
        return []
    try:
        options = parse(argv)
        out = draft_path(repo, options.out)
        lines, findings = initialise(repo, out, options)
    except UsageError as error:
        return [usage_finding(str(error))]
    except ConfigValueError as error:
        return [
            Finding(
                USAGE_RULE,
                config_label("checkers"),
                str(error),
                "pass a readable --settings file",
            )
        ]
    print("\n".join(lines))
    return findings
