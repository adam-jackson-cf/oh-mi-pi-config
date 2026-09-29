#!/usr/bin/env python3
"""Validate seeded-defect cases: clean cases pass every hidden check, each mutation
alone fails at least one, and every mutated case fails."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from cases import CASES, FIXTURES, build_workspace


def failures(fixture: str, ws: Path) -> tuple[int, int, list[str]]:
    result = subprocess.run(["sh", str(FIXTURES / fixture / "hidden" / "check.sh"), str(ws)],
                            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    score = json.loads(lines[-1])
    failed = [line.strip() for line in (result.stdout + result.stderr).splitlines() if "FAIL" in line]
    return score["passed"], score["total"], failed


def main() -> int:
    ok = True
    for case in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            build_workspace(case, ws)
            passed, total, failed = failures(case.fixture, ws)
        clean = not case.mutations
        valid = (passed == total) if clean else (passed < total)
        ok &= valid
        print(f"{case.id:18} {passed:>2}/{total:<3} {'PASS' if valid else 'FAIL'}")
        for mutation in case.mutations:
            with tempfile.TemporaryDirectory() as tmp:
                ws = Path(tmp) / "ws"
                build_workspace(case, ws, (mutation,))
                passed, total, failed = failures(case.fixture, ws)
            valid = passed < total
            ok &= valid
            print(f"  {mutation.id:22} {passed:>2}/{total:<3} {'PASS' if valid else 'FAIL'}  {'; '.join(failed)[:120]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
