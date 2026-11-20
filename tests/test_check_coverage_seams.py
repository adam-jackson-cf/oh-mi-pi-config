import json
import shutil
import sys
from pathlib import Path

import pytest

from guardrails.host.process import run_text

pytestmark = pytest.mark.seam

SOURCE = Path(__file__).resolve().parents[1] / "scripts/check_coverage.py"
PROCESS_TIMEOUT = 60
PYPROJECT = """
[tool.coverage.run]
source = ["lib"]
omit = []
"""


def git(root: Path, *args: str) -> None:
    run_text(["git", "-C", str(root), *args], timeout=PROCESS_TIMEOUT, check=True)


def checkout(root: Path) -> Path:
    (root / "scripts").mkdir()
    (root / "lib").mkdir()
    (root / "app").mkdir()
    shutil.copy(SOURCE, root / "scripts/check_coverage.py")
    (root / "pyproject.toml").write_text(PYPROJECT)
    (root / "lib/core.py").write_text("x = 1\n")
    (root / "app/tracked.ts").write_text("export const a = 1;\nexport const b = 2;\n")
    (root / "app/untracked.ts").write_text("export const c = 3;\n")
    git(root, "init", "-q")
    git(
        root,
        "add",
        "pyproject.toml",
        "scripts/check_coverage.py",
        "lib/core.py",
        "app/tracked.ts",
    )
    (root / "python.json").write_text(
        json.dumps({
            "files": {
                "lib/core.py": {
                    "summary": {
                        "covered_lines": 2,
                        "num_statements": 2,
                        "covered_branches": 0,
                        "num_branches": 0,
                    }
                }
            }
        })
    )
    (root / "lcov.info").write_text("")
    (root / "node-v8").mkdir()
    return root


def gate(root: Path, line_floor: str) -> tuple[int, str]:
    result = run_text(
        [
            sys.executable,
            str(root / "scripts/check_coverage.py"),
            "--python-json",
            str(root / "python.json"),
            "--lcov",
            str(root / "lcov.info"),
            "--node-v8",
            str(root / "node-v8"),
            "--line-floor",
            line_floor,
            "--branch-floor",
            "0",
        ],
        timeout=PROCESS_TIMEOUT,
    )
    return result.returncode, result.stdout + result.stderr


def test_main_counts_tracked_scripts_no_test_loads_and_ignores_untracked_ones(
    tmp_path: Path,
) -> None:
    root = checkout(tmp_path)
    code, output = gate(root, "50")
    assert code == 0, output
    assert "lines 50.00%" in output
    assert "TypeScript 0.00%" in output
    assert "app/tracked.ts" in output
    assert "app/untracked.ts" not in output


def test_main_fails_below_the_line_floor(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    code, output = gate(root, "51")
    assert code == 1, output
    assert "Coverage is below its floor." in output
