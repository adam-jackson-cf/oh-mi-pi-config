from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path


def run_command(
    argv: list[str], cwd: Path, environment: Mapping[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=dict(environment),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
