"""``omp-guardrails init [--greenfield | --adopt | --extend] [options]``.

Modes:

- ``--greenfield`` (default when the repo has no ``.omp/gate.json``): write the test profile
  from the template, a schema-v2 gate that runs the reference checks, the tracked pre-commit
  hook, and print the dependency pin line.
- ``--adopt`` (a repo with its own checkers): as greenfield, but existing ``checks.commit`` and
  ``checks.ci`` keep running and the reference checks run after them (shadow comparison).
  ``--settings FILE`` adds per-checker settings under the profile's ``guardrails``. Existing
  checks, hooks and values are never removed or replaced.
- ``--extend`` (surface only): add the profile sections and keys the repo's profile lacks, such
  as ``verification_surface``. It touches nothing else.

Every mode records the detected language servers in ``command_notes.lsp`` and fails when a
server is missing from ``PATH`` unless ``--allow-missing-lsp`` is given. Init never creates an
empty folder and never overwrites a recorded value, so a second run changes nothing.

Without a mode flag, a repo whose gate already runs the reference re-runs greenfield (a no-op
apart from filling gaps); any other existing gate needs an explicit ``--adopt`` or ``--extend``.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from omp_guardrails.config.profile import PROFILE_PATH, ProfileError
from omp_guardrails.findings import Finding
from omp_guardrails.init import gate_config, hook, lsp, pin, profile_fill

USAGE_RULE = "PROFILE.complete"
DEFAULT_CHECKS = ("profile-check",)
NOT_GATE_CHECKS = frozenset({"init", "gate"})


class UsageError(Exception):
    """The command line cannot be acted on."""


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="omp-guardrails init", description=__doc__)
    modes = command.add_mutually_exclusive_group()
    for mode in ("greenfield", "adopt", "extend"):
        modes.add_argument(f"--{mode}", dest="mode", action="store_const", const=mode)
    command.add_argument("--ci-host", help="ciHost to record in .omp/gate.json")
    command.add_argument(
        "--reference-command",
        default="omp-guardrails",
        help="how the repo invokes the pinned reference, e.g. 'uv run omp-guardrails'",
    )
    command.add_argument(
        "--check",
        action="append",
        dest="checks",
        help="reference subcommand the gate runs (repeatable; default profile-check)",
    )
    command.add_argument("--settings", type=Path, help="JSON object of guardrails settings")
    command.add_argument("--allow-missing-lsp", action="store_true")
    command.add_argument("--pin-url")
    command.add_argument("--pin-commit")
    command.add_argument("--pin-subdirectory")
    return command


def parse(argv: list[str]) -> argparse.Namespace:
    try:
        return parser().parse_args(argv)
    except SystemExit as error:
        raise UsageError(f"invalid init arguments: {shlex.join(argv)}") from error


def gate_checks(names: list[str] | None) -> list[str]:
    from omp_guardrails.cli import SUBCOMMANDS

    checks = list(dict.fromkeys(names or DEFAULT_CHECKS))
    unknown = [name for name in checks if name not in SUBCOMMANDS or name in NOT_GATE_CHECKS]
    if unknown:
        raise UsageError(f"--check names no gate subcommand: {unknown}")
    return checks


def resolve_mode(requested: str | None, gate: dict | None, reference: list[str]) -> str:
    if requested:
        return requested
    if gate is None:
        return "greenfield"
    checks = gate.get("checks")
    commit = checks.get("commit") if isinstance(checks, dict) else None
    if gate_config.runs_reference(commit, reference):
        return "greenfield"
    raise UsageError(f"{gate_config.GATE_PATH} already declares checks; choose --adopt or --extend")


def write_if_changed(path: Path, text: str | None, changes: list[str], label: str) -> None:
    if text is None or (path.exists() and path.read_text(encoding="utf-8") == text):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    changes.append(f"wrote {label}")


def apply_profile(repo: Path, options: argparse.Namespace, changes: list[str]) -> list[str]:
    settings = profile_fill.load_settings(options.settings)
    notes = lsp.notes(lsp.detect(repo))
    text, report = profile_fill.planned_profile(repo, settings, notes)
    write_if_changed(repo / PROFILE_PATH, text, changes, PROFILE_PATH)
    return report


def apply_gate(
    repo: Path, gate: dict | None, mode: str, options: argparse.Namespace, changes: list[str]
) -> list[str]:
    planned = gate_config.planned_gate(
        gate,
        adopt=mode == "adopt",
        reference=shlex.split(options.reference_command),
        checks=gate_checks(options.checks),
        ci_host=options.ci_host,
    )
    if planned != gate:
        write_if_changed(
            repo / gate_config.GATE_PATH,
            gate_config.render(planned),
            changes,
            gate_config.GATE_PATH,
        )
    note = gate_config.ci_host_note(gate, options.ci_host)
    return [note] if note else []


def pin_report(options: argparse.Namespace) -> tuple[list[str], list[Finding]]:
    explicit = pin.PinSource(options.pin_url, options.pin_commit, options.pin_subdirectory)
    line, findings = pin.pin_line(pin.resolve(explicit, pin.installed_source()))
    if not line:
        return [], findings
    return [
        "pin: add this line to the repo's dev dependencies and lock it:",
        f"  {line}",
    ], []


def initialise(repo: Path, options: argparse.Namespace) -> tuple[list[str], list[Finding]]:
    """Apply the selected mode; return report lines and setup-gap findings."""
    reference = shlex.split(options.reference_command)
    gate = gate_config.load_gate(repo)
    mode = resolve_mode(options.mode, gate, reference)
    if mode == "extend" and not (repo / PROFILE_PATH).exists():
        raise UsageError(f"--extend needs an existing {PROFILE_PATH}; run --greenfield or --adopt")
    if mode != "extend":
        gate_checks(options.checks)
    changes: list[str] = []
    report = apply_profile(repo, options, changes)
    findings: list[Finding] = []
    if mode != "extend":
        report += apply_gate(repo, gate, mode, options, changes)
        findings += hook.install_hook(repo, changes)
        pin_lines, pin_findings = pin_report(options)
        report += pin_lines
        findings += pin_findings
    lsp_findings, lsp_lines = lsp.readiness(
        lsp.detect(repo), allow_missing=options.allow_missing_lsp
    )
    findings += lsp_findings
    summary = changes or ["no changes"]
    return [f"init ({mode}): {line}" for line in summary] + report + lsp_lines, findings


def usage_finding(message: str) -> Finding:
    return Finding(USAGE_RULE, "omp-guardrails init", message, "fix the init arguments")


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """CLI entry: print the report and return the setup gaps that remain."""
    del profile
    if {"-h", "--help"} & set(argv):
        parser().print_help()
        return []
    try:
        lines, findings = initialise(repo, parse(argv))
    except UsageError as error:
        return [usage_finding(str(error))]
    except gate_config.GateError as error:
        return [gate_config.gate_finding(error)]
    except ProfileError as error:
        return [Finding(USAGE_RULE, PROFILE_PATH, str(error), "repair the profile file")]
    print("\n".join(lines))
    return findings
