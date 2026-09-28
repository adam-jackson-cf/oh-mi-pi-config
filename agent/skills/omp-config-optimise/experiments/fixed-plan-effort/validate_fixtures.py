#!/usr/bin/env python3
"""Validate that each frozen fixture fails before and passes after its reference overlay."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class CheckResult:
    returncode: int
    passed: int
    total: int


def run_check(fixture: Path, workspace: Path) -> CheckResult:
    result = subprocess.run(
        [fixture / "hidden" / "check.sh", workspace],
        capture_output=True,
        text=True,
        timeout=10,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"{fixture.name}: checker printed no JSON\nstderr: {result.stderr}")
    try:
        payload = json.loads(lines[-1])
        passed = payload["passed"]
        total = payload["total"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError(f"{fixture.name}: invalid final checker JSON: {lines[-1]!r}") from error
    if not isinstance(passed, int) or not isinstance(total, int):
        raise RuntimeError(f"{fixture.name}: checker counts must be integers")
    return CheckResult(result.returncode, passed, total)


def validate_fixture(fixture: Path) -> tuple[str, CheckResult, CheckResult, bool]:
    with tempfile.TemporaryDirectory(prefix=f"{fixture.name}-") as directory:
        workspace = Path(directory) / "workspace"
        shutil.copytree(fixture / "repo", workspace)
        before = run_check(fixture, workspace)
        shutil.copytree(fixture / "reference", workspace, dirs_exist_ok=True)
        after = run_check(fixture, workspace)
    expected_failure = before.returncode != 0 and before.passed < before.total
    expected_success = after.returncode == 0 and after.passed == after.total
    return fixture.name, before, after, expected_failure and expected_success


def main() -> int:
    fixtures = sorted(path for path in ROOT.iterdir() if path.is_dir())
    results = [validate_fixture(fixture) for fixture in fixtures]
    print(f"{'fixture':32}  before     after      result")
    print(f"{'-' * 32}  ---------  ---------  ------")
    for fixture_id, before, after, valid in results:
        status = "PASS" if valid else "FAIL"
        print(f"{fixture_id:32}  {before.passed:>2}/{before.total:<6}  {after.passed:>2}/{after.total:<6}  {status}")
    return 0 if all(valid for *_, valid in results) else 1


if __name__ == "__main__":
    sys.exit(main())
