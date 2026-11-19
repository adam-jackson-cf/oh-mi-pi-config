from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from typing import IO, TypedDict, Unpack

Argument = str | os.PathLike[str]
Directory = str | os.PathLike[str] | None


class Launch(TypedDict, total=False):
    cwd: Directory
    env: Mapping[str, str] | None
    timeout: float | None
    check: bool


def environment_copy(launch: Launch) -> dict[str, str] | None:
    env = launch.get("env")
    return None if env is None else dict(env)


def run_text(
    argv: Sequence[Argument],
    *,
    data: str | None = None,
    errors: str | None = None,
    **launch: Unpack[Launch],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=launch.get("cwd"),
        env=environment_copy(launch),
        input=data,
        capture_output=True,
        text=True,
        errors=errors,
        timeout=launch.get("timeout"),
        check=launch.get("check", False),
    )


def run_bytes(
    argv: Sequence[Argument],
    *,
    data: bytes | None = None,
    **launch: Unpack[Launch],
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        cwd=launch.get("cwd"),
        env=environment_copy(launch),
        input=data,
        capture_output=True,
        timeout=launch.get("timeout"),
        check=launch.get("check", False),
    )


def run_attached(
    argv: Sequence[Argument], **launch: Unpack[Launch]
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        cwd=launch.get("cwd"),
        env=environment_copy(launch),
        timeout=launch.get("timeout"),
        check=launch.get("check", False),
    )


def start_piped(
    argv: Sequence[Argument], **launch: Unpack[Launch]
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        list(argv),
        cwd=launch.get("cwd"),
        env=environment_copy(launch),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def start_logged(
    argv: Sequence[Argument], *, output: IO[bytes], **launch: Unpack[Launch]
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        list(argv),
        cwd=launch.get("cwd"),
        env=environment_copy(launch),
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
