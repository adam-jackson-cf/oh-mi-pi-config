"""SURFACE.lever-isolation: the lever's doctor runs isolated, fails visibly and cleans up.

``omp-guardrails lever-selftest`` runs the profile's ``lever_command`` twice from the repo root,
each time in its own session (process group) with fresh temporary data and evidence paths passed
through ``data_dir_arg`` and ``evidence_dir_arg`` (an argument ending in ``=`` is joined to its
value):

1. Healthy run, with an empty data directory: it must exit 0.
2. Broken-environment run: by default the data-directory argument names a regular file, which no
   usable instance can use, so doctor must fail. It must exit non-zero and print the profile's
   ``doctor_marker`` on stdout or stderr.

After each run the evidence directory must exist, and no process may remain in the lever's
process group or carry the run's temporary path in its command line (read with ``ps``, or
from ``/proc`` where ``ps`` is not installed). Leftovers are reported and then killed. Ports
are not probed separately: a port is held by a process, so the process check covers it. POSIX
only; elsewhere the self-test reports that it cannot run.

Profile ``guardrails.lever_selftest`` settings (all optional):

- ``doctor_args``: arguments placed after the lever command, e.g. ``["doctor"]`` (default none).
- ``failure_env``: environment overrides that break a real dependency for run 2 instead of the
  data-directory-is-a-file default, e.g. a database URL pointing at a closed port.
- ``timeout_s``: per-run timeout in seconds (default 120).
- ``settle_s``: how long to wait for exiting children before calling them leftovers (default 2).
"""

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

from omp_guardrails.child_env import repo_environment
from omp_guardrails.config.profile import PROFILE_PATH
from omp_guardrails.findings import Finding
from omp_guardrails.structure.inventory import as_list, settings
from omp_guardrails.surface.feature_map import surface

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


def number(config: dict, key: str, default: float) -> float:
    value = config.get(key)
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
    """(pid, command line) of every visible process: ``ps``, else Linux ``/proc``."""
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False
        )
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
        table.append((int(entry.name), raw.replace(b"\0", b" ").decode(errors="replace").strip()))
    return table


def marked_processes(marker: str) -> list[tuple[int, str]]:
    """Processes whose command line contains the run's temporary path."""
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
    repo: Path, command: list[str], env: dict[str, str], scratch: Path, config: dict
) -> LeverRun:
    """Run the lever in its own session; output goes to a file so a child holding stdout
    cannot block the wait (it is reported as a leftover instead)."""
    log = scratch / f"lever-output-{time.monotonic_ns()}.log"
    with log.open("wb") as output:
        try:
            process = subprocess.Popen(
                command,
                cwd=repo,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            return LeverRun(None, f"cannot start lever: {error}", ())
        try:
            returncode: int | None = process.wait(
                timeout=number(config, "timeout_s", DEFAULT_TIMEOUT_S)
            )
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            returncode = None
    left = leftovers(process.pid, str(scratch), number(config, "settle_s", DEFAULT_SETTLE_S))
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
                PROFILE_PATH,
                f"{label} run timed out: {tail(result.output)}",
                "make doctor finish or fail within timeout_s",
            )
        )
    if result.leftovers:
        found.append(
            Finding(
                RULE,
                PROFILE_PATH,
                f"{label} run left processes behind: {', '.join(result.leftovers)}",
                "kill the lever's whole process group on every exit path",
            )
        )
    if result.returncode is not None and not evidence.is_dir():
        found.append(
            Finding(
                RULE,
                PROFILE_PATH,
                f"{label} run did not leave the evidence directory",
                "create the evidence directory and keep it after cleanup",
            )
        )
    return found


def run(repo: Path, profile: dict, argv: list[str]) -> list[Finding]:
    """``omp-guardrails lever-selftest``."""
    del argv
    values = surface(profile)
    if values is None:
        return []
    config = settings(profile, CHECKER)
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
                PROFILE_PATH,
                "verification_surface needs lever_command, doctor_marker, data_dir_arg and "
                "evidence_dir_arg",
                "record them in the test profile",
            )
        ]
    if not hasattr(os, "killpg"):
        return [Finding(RULE, PROFILE_PATH, "process-group checks need POSIX", "run in CI")]
    base = [*lever, *as_list(config.get("doctor_args"))]
    failure_env = config.get("failure_env")
    found: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="omp-lever-selftest-") as scratch:
        root = Path(scratch).resolve()
        healthy_data, healthy_evidence = root / "healthy-data", root / "healthy-evidence"
        healthy_data.mkdir()
        healthy = run_lever(
            repo,
            [
                *base,
                *path_arguments(data_flag, healthy_data),
                *path_arguments(evidence_flag, healthy_evidence),
            ],
            repo_environment(),
            root,
            config,
        )
        if healthy.returncode not in (None, 0):
            found.append(
                Finding(
                    RULE,
                    PROFILE_PATH,
                    f"healthy doctor run exited {healthy.returncode}: {tail(healthy.output)}",
                    "make doctor pass in a fresh temporary data directory",
                )
            )
        found.extend(run_findings("healthy", healthy, healthy_evidence))

        broken_evidence = root / "broken-evidence"
        env = repo_environment()
        if isinstance(failure_env, dict) and failure_env:
            broken_data = root / "broken-data"
            broken_data.mkdir()
            env.update({str(key): str(value) for key, value in failure_env.items()})
        else:
            broken_data = root / "broken-data-is-a-file"
            broken_data.write_text("not a data directory\n", encoding="utf-8")
        broken = run_lever(
            repo,
            [
                *base,
                *path_arguments(data_flag, broken_data),
                *path_arguments(evidence_flag, broken_evidence),
            ],
            env,
            root,
            config,
        )
        if broken.returncode == 0:
            found.append(
                Finding(
                    RULE,
                    PROFILE_PATH,
                    "doctor passed in a broken environment (unusable data directory)",
                    "make doctor prove the instance is usable, not only that it started",
                )
            )
        elif broken.returncode is not None and doctor_marker not in broken.output:
            found.append(
                Finding(
                    RULE,
                    PROFILE_PATH,
                    f"doctor failure did not print the doctor marker '{doctor_marker}': "
                    f"{tail(broken.output)}",
                    "print the profile's doctor marker on every doctor failure",
                )
            )
        found.extend(run_findings("broken-environment", broken, broken_evidence))
    return found
