"""``omp-guardrails init --repo DIR --record DIR [options]``: draft the repo's personal record.

Init reads the repository and writes only into the record (``<oh-mi-pi-config
root>/repos/<owner>/<name>/``, gitignored in the harness and never committed):

1. discovers the repository's own controls (``discovery``): make targets, hook managers, CI
   jobs and, through ``gh``, the default branch's required checks;
2. writes ``profile.md`` from the test-guidance template, keeping every recorded value
   (``profile_fill``); ``--settings FILE`` adds per-checker settings under ``guardrails``;
3. writes ``gate.json`` with the absent fields drafted from the discovery (``record_gate``);
4. writes proposed native-tool configs to ``proposed/`` (``native_config``); the owner applies
   them to the repository as a fix;
5. records the dynaflows contract version (``contract``) as its last write;
6. records the detected language servers in ``command_notes.lsp`` and runs each server's canary
   references query (``lsp``). A server missing from ``PATH`` is a setup gap unless
   ``--allow-missing-lsp`` is given; a server that fails the canary always is.

``--extend`` brings an existing record to the current format and contract (for example after
a contract bump) and refuses to create a record. The report lists what was discovered and every
value the owner must confirm. Init never
overwrites a recorded value, never writes an approval and never turns dynaflows on, so a second
run changes nothing.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from omp_guardrails.config.profile import PROFILE_FILE, PROFILE_PATH, ProfileError, load_profile
from omp_guardrails.findings import Finding
from omp_guardrails.init import contract, discovery, lsp, native_config, profile_fill, record_gate

USAGE_RULE = "PROFILE.complete"
PROPOSED_DIR = "proposed"


class UsageError(Exception):
    """The command line cannot be acted on."""


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="omp-guardrails init", description=__doc__)
    command.add_argument(
        "--extend", action="store_true", help="update an existing record; never create one"
    )
    command.add_argument("--commit-command", help="the repo's own commit gate command")
    command.add_argument("--ci-command", help="the repo's own CI gate command")
    command.add_argument(
        "--required-context",
        help="ci.requiredContext: status context or check run that must be green on the head",
    )
    command.add_argument(
        "--runner", help="ci.runner: role the machine's runner config resolves (e.g. ci-gate)"
    )
    command.add_argument("--settings", type=Path, help="JSON object of guardrails settings")
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
    for flag in ("commit_command", "ci_command", "required_context", "runner"):
        value = getattr(options, flag)
        if value is not None and not value.strip():
            raise UsageError(f"--{flag.replace('_', '-')} must not be empty")
    return options


def write_if_changed(path: Path, text: str | None, changes: list[str], label: str) -> None:
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
    gate: dict | None,
    found: discovery.Discovery,
    options: argparse.Namespace,
    changes: list[str],
) -> list[str]:
    planned, confirmations = record_gate.planned_gate(
        gate,
        found,
        commit=options.commit_command,
        ci_command=options.ci_command,
        required_context=options.required_context,
        runner=options.runner,
    )
    if planned != gate:
        write_if_changed(
            record / record_gate.GATE_FILE,
            record_gate.render(planned),
            changes,
            record_gate.GATE_PATH,
        )
    return confirmations


def apply_native(repo: Path, record: Path, changes: list[str]) -> list[str]:
    if not (repo / "pyproject.toml").is_file():
        return []
    planned, report = native_config.planned_configs(repo, optional_profile(record))
    for name, text in planned.items():
        label = f"record:{PROPOSED_DIR}/{name} (apply it to the repository as a fix)"
        write_if_changed(record / PROPOSED_DIR / name, text, changes, label)
    return report


def optional_profile(record: Path) -> dict:
    try:
        return load_profile(record)
    except ProfileError:
        return {}


def apply_contract(record: Path, version: int, changes: list[str]) -> list[str]:
    """Record ``version`` in the gate on disk; run after every other write succeeded."""
    gate = record_gate.load_gate(record)
    if gate is None:
        return [f"{contract.FIELD}: no {record_gate.GATE_PATH} was written"]
    planned, note = contract.planned(gate, version)
    if planned != gate:
        write_if_changed(
            record / record_gate.GATE_FILE,
            record_gate.render(planned),
            changes,
            f"{contract.FIELD} {version} in {record_gate.GATE_PATH}",
        )
    return [note] if note else []


def initialise(
    repo: Path, record: Path, options: argparse.Namespace
) -> tuple[list[str], list[Finding]]:
    """Draft the record; return report lines and setup-gap findings."""
    gate = record_gate.load_gate(record)
    if gate is not None:
        contract.recorded(gate)
    elif options.extend:
        raise UsageError(f"--extend needs an existing {record_gate.GATE_PATH}; run init without it")
    version = contract.current()
    found = discovery.discover(repo, offline=options.offline)
    changes: list[str] = []
    report = found.report() + apply_profile(repo, record, options, changes)
    report += apply_gate(record, gate, found, options, changes)
    report += apply_native(repo, record, changes)
    report += apply_contract(record, version, changes)
    lsp_findings, lsp_lines = lsp.readiness(
        repo,
        lsp.detect(repo),
        allow_missing=options.allow_missing_lsp,
        timeout=options.lsp_timeout,
    )
    summary = [f"init: {line}" for line in changes or ["no changes"]]
    return summary + report + lsp_lines, lsp_findings


def usage_finding(message: str) -> Finding:
    return Finding(USAGE_RULE, "omp-guardrails init", message, "fix the init arguments")


def run_init(repo: Path, record: Path, argv: list[str]) -> list[Finding]:
    """CLI entry: print the report and return the setup gaps that remain."""
    if {"-h", "--help"} & set(argv):
        parser().print_help()
        return []
    try:
        lines, findings = initialise(repo, record, parse(argv))
    except (UsageError, contract.ContractError) as error:
        return [usage_finding(str(error))]
    except record_gate.GateError as error:
        return [record_gate.gate_finding(error)]
    except ProfileError as error:
        return [Finding(USAGE_RULE, PROFILE_PATH, str(error), "repair the record's profile")]
    print("\n".join(lines))
    return findings
