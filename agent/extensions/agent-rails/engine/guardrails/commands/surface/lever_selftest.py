from __future__ import annotations

import contextlib
import os
import shlex
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from guardrails.commands.surface.feature_map import surface
from guardrails.context.config.reader import checker_settings
from guardrails.foundation.child_env import repo_environment
from guardrails.foundation.findings import Finding, config_label
from guardrails.foundation.inventory import as_list
from guardrails.host.process import run_text, start_logged

RULE = "SURFACE.lever-isolation"
CHECKER = "lever_selftest"
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_SETTLE_S = 2.0
POLL_S = 0.05
OUTPUT_TAIL = 400


@dataclass(frozen=True)
class LeverRun:
    returncode: int | None
    output: str
    leftovers: tuple[str, ...]


def number(options: dict[str, Any], key: str, default: float) -> float:
    value = options.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return default
    return float(value)


def path_arguments(flag: str, value: Path) -> list[str]:
    return [f"{flag}{value}"] if flag.endswith("=") else [flag, str(value)]


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_table() -> list[tuple[int, str]]:
    try:
        result = run_text(["ps", "-axo", "pid=,command="], check=False)
    except OSError:
        return proc_table()
    table = []
    for line in result.stdout.splitlines():
        pid_text, _, command = line.strip().partition(" ")
        if pid_text.isdigit():
            table.append((int(pid_text), command.strip()))
    return table


def proc_table() -> list[tuple[int, str]]:
    table = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        table.append((
            int(entry.name),
            raw.replace(b"\0", b" ").decode(errors="replace").strip(),
        ))
    return table


def marked_processes(marker: str) -> list[tuple[int, str]]:
    return [
        (pid, command)
        for pid, command in process_table()
        if marker in command and pid != os.getpid()
    ]


def leftovers(pgid: int, marker: str, settle_s: float) -> tuple[str, ...]:
    deadline = time.monotonic() + settle_s
    while True:
        alive = group_alive(pgid)
        marked = marked_processes(marker)
        if not alive and not marked:
            return ()
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_S)
    found = []
    if alive:
        found.append(f"process group {pgid}")
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGKILL)
    for pid, command in marked:
        found.append(f"pid {pid} ({command[:120]})")
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    return tuple(found)


def run_lever(
    repo: Path,
    command: list[str],
    env: dict[str, str],
    scratch: Path,
    options: dict[str, Any],
) -> LeverRun:
    log = scratch / f"lever-output-{time.monotonic_ns()}.log"
    with log.open("wb") as output:
        try:
            process = start_logged(command, output=output, cwd=repo, env=env)
        except OSError as error:
            return LeverRun(None, f"cannot start lever: {error}", ())
        try:
            returncode: int | None = process.wait(
                timeout=number(options, "timeout_s", DEFAULT_TIMEOUT_S)
            )
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            returncode = None
    left = leftovers(
        process.pid, str(scratch), number(options, "settle_s", DEFAULT_SETTLE_S)
    )
    text = log.read_bytes().decode(errors="replace")
    return LeverRun(returncode, text, left)


def tail(text: str) -> str:
    text = " ".join(text.split())
    return text[-OUTPUT_TAIL:] if text else "(no output)"


def run_findings(label: str, result: LeverRun, evidence: Path) -> list[Finding]:
    found = []
    if result.returncode is None:
        found.append(
            Finding(
                RULE,
                config_label("tests"),
                f"{label} run timed out: {tail(result.output)}",
                "make doctor finish or fail within timeout_s",
            )
        )
    if result.leftovers:
        found.append(
            Finding(
                RULE,
                config_label("tests"),
                f"{label} run left processes behind: {', '.join(result.leftovers)}",
                "kill the lever's whole process group on every exit path",
            )
        )
    if result.returncode is not None and not evidence.is_dir():
        found.append(
            Finding(
                RULE,
                config_label("tests"),
                f"{label} run did not leave the evidence directory",
                "create the evidence directory and keep it after cleanup",
            )
        )
    return found


def run(repo: Path, config: dict[str, Any], argv: list[str]) -> list[Finding]:
    del argv
    values = surface(config)
    if values is None:
        return []
    options = checker_settings(config, CHECKER)
    try:
        lever = shlex.split(str(values.get("lever_command") or ""))
    except ValueError:
        lever = []
    doctor_marker = str(values.get("doctor_marker") or "")
    data_flag = str(values.get("data_dir_arg") or "")
    evidence_flag = str(values.get("evidence_dir_arg") or "")
    if not (lever and doctor_marker and data_flag and evidence_flag):
        return [
            Finding(
                RULE,
                config_label("tests"),
                "verification_surface needs lever_command, doctor_marker, data_dir_arg and "
                "evidence_dir_arg",
                "set them in tests.verification_surface in .agentrails/config.json",
            )
        ]
    if not hasattr(os, "killpg"):
        return [
            Finding(
                RULE,
                config_label("tests"),
                "process-group checks need POSIX",
                "run in CI",
            )
        ]
    doctor = DoctorCommand(
        repo,
        [*lever, *as_list(options.get("doctor_args"))],
        data_flag,
        evidence_flag,
        options,
    )
    found: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="agent-rails-lever-selftest-") as scratch:
        root = Path(scratch).resolve()
        found.extend(healthy_findings(doctor, root))
        found.extend(
            broken_findings(doctor, root, options.get("failure_env"), doctor_marker)
        )
    return found


@dataclass(frozen=True)
class DoctorCommand:
    repo: Path
    base: list[str]
    data_flag: str
    evidence_flag: str
    options: dict[str, Any]

    def run(
        self, data: Path, evidence: Path, env: dict[str, str], root: Path
    ) -> LeverRun:
        argv = [
            *self.base,
            *path_arguments(self.data_flag, data),
            *path_arguments(self.evidence_flag, evidence),
        ]
        return run_lever(self.repo, argv, env, root, self.options)


def healthy_findings(doctor: DoctorCommand, root: Path) -> list[Finding]:
    found = []
    healthy_data, healthy_evidence = root / "healthy-data", root / "healthy-evidence"
    healthy_data.mkdir()
    healthy = doctor.run(healthy_data, healthy_evidence, repo_environment(), root)
    if healthy.returncode not in (None, 0):
        found.append(
            Finding(
                RULE,
                config_label("tests"),
                f"healthy doctor run exited {healthy.returncode}: {tail(healthy.output)}",
                "make doctor pass in a fresh temporary data directory",
            )
        )
    found.extend(run_findings("healthy", healthy, healthy_evidence))
    return found


def broken_findings(
    doctor: DoctorCommand, root: Path, failure_env: object, doctor_marker: str
) -> list[Finding]:
    found = []
    broken_evidence = root / "broken-evidence"
    env = repo_environment()
    if isinstance(failure_env, dict) and failure_env:
        broken_data = root / "broken-data"
        broken_data.mkdir()
        env.update({str(key): str(value) for key, value in failure_env.items()})
    else:
        broken_data = root / "broken-data-is-a-file"
        broken_data.write_text("not a data directory\n", encoding="utf-8")
    broken = doctor.run(broken_data, broken_evidence, env, root)
    if broken.returncode == 0:
        found.append(
            Finding(
                RULE,
                config_label("tests"),
                "doctor passed in a broken environment (unusable data directory)",
                "make doctor prove the instance is usable, not only that it started",
            )
        )
    elif broken.returncode is not None and doctor_marker not in broken.output:
        found.append(
            Finding(
                RULE,
                config_label("tests"),
                f"doctor failure did not print the doctor marker '{doctor_marker}': "
                f"{tail(broken.output)}",
                "print the configured doctor marker on every doctor failure",
            )
        )
    found.extend(run_findings("broken-environment", broken, broken_evidence))
    return found
