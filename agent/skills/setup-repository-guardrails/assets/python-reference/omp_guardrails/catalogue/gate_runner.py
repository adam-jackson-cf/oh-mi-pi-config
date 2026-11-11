"""Run one declared gate check, propagate its failure, or verify the gate wiring.

``omp-guardrails gate <name>`` runs ``checks.<name>`` from ``.omp/gate.json`` (schemaVersion 2)
in the repository root, streaming its output. Any non-zero or signal exit is a
``TEST.failure-propagation`` finding, so a failed prerequisite never reports a passing gate.
An unreadable gate file or a non-argv check is a ``GATE.local-ci-parity`` finding.

``omp-guardrails gate wiring`` checks that the hook and CI entry points still call the gate,
and that a declared ``ci`` names a non-empty ``requiredContext`` and ``runner`` (the retired
``ciHost`` is a finding). It reads ``guardrails.gate`` from the profile:

- ``checks``: ``{"<name>": [argv ...]}`` exact argv each declared check must keep (optional);
- ``require_path_check``: when true, ``pathCheck`` must be a non-empty argv;
- ``files``: ``[{"path": "<repo file>", "rule": "<rule id>", "lines": ["<exact line>", ...]}]``
  lines (compared after stripping whitespace) that the hook config or CI workflow must contain,
  for example the step that runs ``omp-guardrails gate ci``;
- ``unset_env``: variable names removed from the check's environment (``GIT_*`` always is);
- ``timeout_s``: optional wall limit for one check run.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from omp_guardrails.catalogue.scratch import clean_environment
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, settings

GATE_PATH = ".omp/gate.json"
SCHEMA_VERSION = 2
PARITY = "GATE.local-ci-parity"
PROPAGATION = "TEST.failure-propagation"
RETIRED_CI_HOST = "ciHost is retired; declare ci.requiredContext and ci.runner"


class GateConfigError(Exception):
    """``.omp/gate.json`` needs repair before any check can run."""


def load_gate(repo: Path) -> dict:
    try:
        gate = json.loads((repo / GATE_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise GateConfigError(f"{GATE_PATH} does not exist") from error
    except (OSError, UnicodeError, ValueError) as error:
        raise GateConfigError(f"{GATE_PATH} is not readable JSON: {error}") from error
    if not isinstance(gate, dict) or gate.get("schemaVersion") != SCHEMA_VERSION:
        raise GateConfigError(f"{GATE_PATH} must be an object with schemaVersion {SCHEMA_VERSION}")
    return gate


def is_argv(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(part, str) and part.strip() for part in value)
    )


def check_argv(gate: dict, name: str) -> list[str]:
    checks = gate.get("checks")
    command = checks.get(name) if isinstance(checks, dict) else None
    if not is_argv(command):
        raise GateConfigError(f"checks.{name} must be a non-empty argv of strings")
    return command


def config_finding(message: str) -> Finding:
    return Finding(PARITY, GATE_PATH, message, "repair .omp/gate.json to declare the check argv")


def run_check(repo: Path, profile: dict, name: str) -> list[Finding]:
    try:
        command = check_argv(load_gate(repo), name)
    except GateConfigError as error:
        return [config_finding(str(error))]
    options = settings(profile, "gate")
    timeout = options.get("timeout_s")
    sys.stdout.flush()
    try:
        result = subprocess.run(
            command,
            cwd=repo,
            env=clean_environment(as_list(options.get("unset_env"))),
            check=False,
            timeout=timeout if isinstance(timeout, (int, float)) else None,
        )
    except OSError as error:
        return [
            Finding(
                PARITY,
                GATE_PATH,
                f"checks.{name} cannot start: {error}",
                "install the declared runner or repair its argv",
            )
        ]
    except subprocess.TimeoutExpired:
        return [failure(name, f"exceeded guardrails.gate.timeout_s ({timeout}s)")]
    if result.returncode == 0:
        return []
    status = result.returncode
    detail = f"was killed by signal {-status}" if status < 0 else f"exited {status}"
    return [failure(name, detail)]


def failure(name: str, detail: str) -> Finding:
    return Finding(
        PROPAGATION,
        GATE_PATH,
        f"checks.{name} {detail}",
        "fix the failing check; a failed prerequisite never passes the gate",
    )


def wiring_findings(repo: Path, profile: dict) -> list[Finding]:
    try:
        gate = load_gate(repo)
    except GateConfigError as error:
        return [config_finding(str(error))]
    options = settings(profile, "gate")
    findings = ci_findings(gate) + expected_check_findings(gate, options.get("checks"))
    if options.get("require_path_check") and not is_argv(gate.get("pathCheck")):
        findings.append(
            Finding(
                "GATE.path-check",
                GATE_PATH,
                "pathCheck is not a non-empty argv",
                "declare the changed-path checker argv in pathCheck",
            )
        )
    entries = options.get("files")
    for entry in entries if isinstance(entries, list) else []:
        findings.extend(file_line_findings(repo, entry))
    return findings


def ci_findings(gate: dict) -> list[Finding]:
    if "ciHost" in gate:
        return [config_finding(RETIRED_CI_HOST)]
    if "ci" not in gate:
        return []
    ci = gate["ci"]
    names = ("requiredContext", "runner")
    if isinstance(ci, dict) and all(
        isinstance(ci.get(name), str) and ci[name].strip() for name in names
    ):
        return []
    return [config_finding("ci must declare non-empty requiredContext and runner strings")]


def expected_check_findings(gate: dict, expected: object) -> list[Finding]:
    findings = []
    for name, argv in (expected if isinstance(expected, dict) else {}).items():
        checks = gate.get("checks")
        actual = checks.get(name) if isinstance(checks, dict) else None
        if actual != argv:
            findings.append(
                config_finding(f"checks.{name} is {actual!r} but the profile declares {argv!r}")
            )
    return findings


def file_line_findings(repo: Path, entry: object) -> list[Finding]:
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        return [
            Finding(
                PARITY,
                ".omp/test-profile.md",
                "guardrails.gate.files entry needs a path",
                "give each wiring entry a path, rule and lines",
            )
        ]
    path, rule = entry["path"], entry.get("rule") or PARITY
    try:
        lines = {line.strip() for line in (repo / path).read_text(encoding="utf-8").splitlines()}
    except (OSError, UnicodeError):
        return [Finding(rule, path, "wiring file is missing", "restore the tracked file")]
    return [
        Finding(rule, path, f"required line {line!r} is missing", "restore the gate entry line")
        for line in as_list(entry.get("lines"))
        if line.strip() not in lines
    ]


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    if argv == ["wiring"]:
        return wiring_findings(repo, profile)
    if len(argv) != 1 or argv[0].startswith("-"):
        return [config_finding("select exactly one declared check name, e.g. 'commit' or 'ci'")]
    return run_check(repo, profile, argv[0])
