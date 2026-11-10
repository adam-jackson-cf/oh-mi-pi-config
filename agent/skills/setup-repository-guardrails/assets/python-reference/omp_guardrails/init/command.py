"""``omp-guardrails init [--greenfield | --adopt | --extend] [options]``.

Modes:

- ``--greenfield`` (default when the repo has no ``.omp/gate.json``): write the test profile
  from the template, a schema-v2 gate that runs the reference checks, the tracked pre-commit
  hook, the CI wiring (``ci_wiring``), the native-tool configs (``native_config``) and the
  pinned reference in ``pyproject.toml`` ``[dependency-groups] dev`` (``pin``).
- ``--adopt`` (a repo with its own checkers): as greenfield, but existing ``checks.commit`` and
  ``checks.ci`` keep running and the reference checks run after them (shadow comparison).
  ``--settings FILE`` adds per-checker settings under the profile's ``guardrails``. Existing
  checks, hooks and values are never removed or replaced.
- ``--extend`` (surface only): add the profile sections and keys the repo's profile lacks, such
  as ``verification_surface``. It touches nothing else.

Every mode records the detected language servers in ``command_notes.lsp`` and runs each
server's canary references query (``lsp``). A server missing from ``PATH`` is a setup gap
unless ``--allow-missing-lsp`` is given; a server that fails the canary always is. Init never
creates an empty folder and never overwrites a recorded value or an existing config, so a
second run changes nothing.

Without a mode flag, a repo whose gate already runs the reference re-runs greenfield (a no-op
apart from filling gaps); any other existing gate needs an explicit ``--adopt`` or ``--extend``.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path

from omp_guardrails.config.profile import PROFILE_PATH, ProfileError
from omp_guardrails.findings import Finding
from omp_guardrails.init import (
    ci_wiring,
    gate_config,
    hook,
    lsp,
    native_config,
    pin,
    profile_fill,
)

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
        "--ci-setup", help="install command the github workflow runs before checks.ci"
    )
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
    command.add_argument(
        "--lsp-timeout",
        type=float,
        default=lsp.DEFAULT_TIMEOUT_S,
        help="seconds each language-server canary may take",
    )
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
) -> tuple[dict, list[str]]:
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
    return planned, [note] if note else []


def apply_ci(
    repo: Path, gate: dict, options: argparse.Namespace, changes: list[str]
) -> tuple[list[str], list[Finding]]:
    path = repo / ci_wiring.WORKFLOW_PATH
    text, report, findings = ci_wiring.planned_wiring(
        gate.get("ciHost"), options.ci_setup, path.exists()
    )
    write_if_changed(path, text, changes, ci_wiring.WORKFLOW_PATH)
    return report, findings


def apply_native(repo: Path, changes: list[str]) -> list[str]:
    if not (repo / "pyproject.toml").is_file():
        return []
    planned, report = native_config.planned_configs(repo, optional_profile(repo))
    for name, text in planned.items():
        write_if_changed(repo / name, text, changes, name)
    return report


def optional_profile(repo: Path) -> dict:
    from omp_guardrails.config.profile import load_profile

    try:
        return load_profile(repo)
    except ProfileError:
        return {}


def apply_pin(
    repo: Path, options: argparse.Namespace, changes: list[str]
) -> tuple[list[str], list[Finding]]:
    explicit = pin.PinSource(options.pin_url, options.pin_commit, options.pin_subdirectory)
    line, findings = pin.pin_line(pin.resolve(explicit, pin.installed_source()))
    pyproject = repo / "pyproject.toml"
    if not line:
        declared = pin.existing_pins(pyproject)
        if declared:
            return [f"pin: kept the existing pin {declared[0]!r}"], []
        return [], findings
    if not pyproject.is_file():
        return ["pin: no pyproject.toml; add this line to the dev dependencies:", f"  {line}"], []
    text, report = pin.planned_pyproject(pyproject.read_text(encoding="utf-8"), line)
    write_if_changed(pyproject, text, changes, "the reference pin in pyproject.toml")
    return [report], []


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
        planned_gate, gate_report = apply_gate(repo, gate, mode, options, changes)
        report += gate_report
        findings += hook.install_hook(repo, changes)
        ci_report, ci_findings = apply_ci(repo, planned_gate, options, changes)
        report += ci_report + apply_native(repo, changes)
        findings += ci_findings
        pin_lines, pin_findings = apply_pin(repo, options, changes)
        report += pin_lines
        findings += pin_findings
    lsp_findings, lsp_lines = lsp.readiness(
        repo,
        lsp.detect(repo),
        allow_missing=options.allow_missing_lsp,
        timeout=options.lsp_timeout,
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
