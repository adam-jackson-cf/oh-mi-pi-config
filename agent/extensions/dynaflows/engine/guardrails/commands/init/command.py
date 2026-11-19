from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Any

from guardrails.commands.init import (
    discovery,
    harness,
    lsp,
    native_config,
    native_languages,
    profile_fill,
    record_gate,
)
from guardrails.context.config.profile import (
    PROFILE_FILE,
    PROFILE_PATH,
    ProfileError,
    load_profile,
)
from guardrails.foundation.findings import Finding
from guardrails.toolkit.dispatch import SUBCOMMANDS

USAGE_RULE = "PROFILE.complete"
PROPOSED_DIR = "proposed"


class UsageError(Exception):
    pass


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="guardrails init", description=SUBCOMMANDS["init"].summary
    )
    command.add_argument("--commit-command", help="the repo's own commit gate command")
    command.add_argument("--ci-command", help="the repo's own CI gate command")
    command.add_argument(
        "--required-context",
        help="ci.requiredContext: status context or check run that must be green on the head",
    )
    command.add_argument(
        "--runner",
        help="ci.runner: role the machine's runner config resolves (e.g. ci-gate)",
    )
    command.add_argument(
        "--settings", type=Path, help="JSON object of guardrails settings"
    )
    command.add_argument(
        "--offline", action="store_true", help="do not ask gh for the required checks"
    )
    command.add_argument("--allow-missing-lsp", action="store_true")
    command.add_argument(
        "--enable-dynaflows",
        action="store_true",
        help="draft dynaflows.enabled true in the record",
    )
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
    for flag in ("commit_command", "ci_command", "required_context", "runner"):
        value = getattr(options, flag)
        if value is not None and not value.strip():
            raise UsageError(f"--{flag.replace('_', '-')} must not be empty")
    return options


def write_if_changed(
    path: Path, text: str | None, changes: list[str], label: str
) -> None:
    if text is None or (path.exists() and path.read_text(encoding="utf-8") == text):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    changes.append(f"wrote {label}")


def apply_profile(
    repo: Path, record: Path, options: argparse.Namespace, changes: list[str]
) -> list[str]:
    settings = profile_fill.load_settings(options.settings)
    notes = lsp.notes(lsp.detect(repo))
    text, report = profile_fill.planned_profile(record, settings, notes)
    write_if_changed(record / PROFILE_FILE, text, changes, PROFILE_PATH)
    return report


def apply_gate(
    record: Path,
    gate: dict[str, Any] | None,
    found: discovery.Discovery,
    options: argparse.Namespace,
    changes: list[str],
) -> list[str]:
    answers = record_gate.GateAnswers(
        commit=options.commit_command,
        ci_command=options.ci_command,
        required_context=options.required_context,
        runner=options.runner,
        dynaflows=options.enable_dynaflows,
    )
    planned, confirmations = record_gate.planned_gate(gate, found, answers)
    if planned != gate:
        write_if_changed(
            record / record_gate.GATE_FILE,
            record_gate.render(planned),
            changes,
            record_gate.GATE_PATH,
        )
    return confirmations


def apply_native(repo: Path, record: Path, changes: list[str]) -> list[str]:
    planned, report = native_languages.planned_configs(repo)
    if (repo / "pyproject.toml").is_file():
        python, lines = native_config.planned_configs(repo, optional_profile(record))
        planned.update(python)
        report = lines + report
    for name, text in planned.items():
        label = f"record:{PROPOSED_DIR}/{name} (apply it to the repository as a fix)"
        write_if_changed(record / PROPOSED_DIR / name, text, changes, label)
    return report


def optional_profile(record: Path) -> dict[str, Any]:
    try:
        return load_profile(record)
    except ProfileError:
        return {}


def initialise(
    repo: Path, record: Path, options: argparse.Namespace
) -> tuple[list[str], list[Finding]]:
    gate = record_gate.load_gate(record)
    found = discovery.discover(repo, offline=options.offline)
    changes: list[str] = []
    report = found.report() + apply_profile(repo, record, options, changes)
    report += apply_gate(record, gate, found, options, changes)
    report += apply_native(repo, record, changes)
    lsp_findings, lsp_lines = lsp.readiness(
        repo,
        lsp.detect(repo),
        allow_missing=options.allow_missing_lsp,
        timeout=options.lsp_timeout,
    )
    summary = [f"init: {line}" for line in changes or ["no changes"]]
    handoff = [f"init: record at {record}."]
    return summary + report + lsp_lines + handoff, lsp_findings


def usage_finding(message: str) -> Finding:
    return Finding(USAGE_RULE, "guardrails init", message, "fix the init arguments")


def is_live(record: Path) -> bool:
    live = harness.RECORDS.resolve()
    return record.resolve() == live or record.resolve().is_relative_to(live)


def run_init(repo: Path, record: Path, argv: list[str]) -> list[Finding]:
    if {"-h", "--help"} & set(argv):
        parser().print_help()
        return []
    live = harness.RECORDS.resolve()
    try:
        options = parse(argv)
        if is_live(record) and len(record.resolve().relative_to(live).parts) != 2:
            raise UsageError(
                f"--record {record} must be a repo's record, {live}/<owner>/<name>"
            )
        lines, findings = initialise(repo, record, options)
    except UsageError as error:
        return [usage_finding(str(error))]
    except record_gate.GateError as error:
        return [record_gate.gate_finding(error)]
    except ProfileError as error:
        return [
            Finding(USAGE_RULE, PROFILE_PATH, str(error), "repair the record's profile")
        ]
    print("\n".join(lines))
    return findings
