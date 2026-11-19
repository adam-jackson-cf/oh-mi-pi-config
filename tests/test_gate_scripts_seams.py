import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest
from child_process import run_text

pytestmark = pytest.mark.seam

SOURCE = Path(__file__).resolve().parents[1]
PROCESS_TIMEOUT = 60
FAKE = """#!{python}
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
entry = [name, *sys.argv[1:]]
if os.environ.get("COVERAGE_FILE"):
    entry.append("COVERAGE_FILE=" + os.environ["COVERAGE_FILE"])
if "audit" in sys.argv or "pip-audit" in sys.argv:
    entry.append("cwd=" + os.getcwd())
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(entry) + "\\n")
if name == "uv" and "--collect-only" in sys.argv:
    sys.exit(int(os.environ.get("FAKE_COLLECT", "5")))
if os.environ.get("FAKE_AUDIT_FAIL") in entry:
    sys.exit(1)
"""


@pytest.fixture
def checkout() -> Any:
    with tempfile.TemporaryDirectory(prefix="gate-scripts-") as scratch:
        root = Path(scratch) / "repo"
        for name in (
            "scripts/run-ci-quality-gates.sh",
            "scripts/run-test-tier.sh",
            "scripts/audit-dependencies.sh",
            "scripts/check_paths.py",
            ".githooks/pre-commit",
        ):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(SOURCE / name, root / name)
        for name in ("tests/unit.test.ts", "tests/flow-seams.test.ts", "readme.md"):
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text("x\n")
        binary = Path(scratch) / "bin"
        binary.mkdir()
        for name in ("uv", "bun", "bunx"):
            (binary / name).write_text(FAKE.format(python=sys.executable))
            (binary / name).chmod(0o755)
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "COVERAGE"))
        }
        env["PATH"] = f"{binary}{os.pathsep}{env['PATH']}"
        env["FAKE_LOG"] = str(Path(scratch) / "calls.jsonl")
        git(root, env, "init", "-q")
        git(root, env, "add", "-A")
        yield root, env


def git(root: Path, env: dict[str, str], *args: str) -> None:
    run_text(
        ["git", "-C", str(root), *args], env=env, timeout=PROCESS_TIMEOUT, check=True
    )


def run(
    root: Path, env: dict[str, str], *argv: str
) -> tuple[int, str, list[list[str]]]:
    Path(env["FAKE_LOG"]).unlink(missing_ok=True)
    result = run_text(["bash", *argv], cwd=root, env=env, timeout=PROCESS_TIMEOUT)
    log = Path(env["FAKE_LOG"])
    calls = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    return result.returncode, result.stdout + result.stderr, calls


def pytest_runs(calls: list[list[str]]) -> list[list[str]]:
    return [
        call
        for call in calls
        if call[:4] == ["uv", "run", "--locked", "pytest"]
        and "--collect-only" not in call
    ]


def bun_runs(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if call[:2] == ["bun", "test"]]


def test_a_pytest_test_without_exactly_one_tier_marker_fails_the_tier(
    checkout: Any,
) -> None:
    root, env = checkout
    code, output, calls = run(
        root,
        {**env, "FAKE_COLLECT": "0"},
        "scripts/run-test-tier.sh",
        "t1",
        "--framework",
        "pytest",
    )
    assert code == 1
    assert "These pytest tests need exactly one tier marker, fast or seam." in output
    assert pytest_runs(calls) == []


def test_t1_runs_fast_pytest_under_the_guards_and_bun_files_outside_the_seams(
    checkout: Any,
) -> None:
    root, env = checkout
    code, output, calls = run(root, env, "scripts/run-test-tier.sh", "t1")
    assert code == 0, output
    [bun] = bun_runs(calls)
    assert bun[2] == "--preload"
    assert bun[4:] == ["./tests/unit.test.ts"]
    [pytest_call] = pytest_runs(calls)
    assert (
        pytest_call[-4:]
        == [
            "-p",
            "guardrails.commands.adapters.pytest.plugin",
            "--omp-guard=fast-tier,user-data",
            "-m",
            "fast",
        ][-4:]
    )


def test_t2_runs_only_seams_under_the_user_data_guard_and_writes_both_reports(
    checkout: Any,
) -> None:
    root, env = checkout
    code, output, calls = run(
        root, env, "scripts/run-test-tier.sh", "t2", "--coverage", "/cov"
    )
    assert code == 0, output
    [bun] = bun_runs(calls)
    assert bun[2:5] == [
        "--coverage",
        "--coverage-reporter=lcov",
        "--coverage-dir=/cov/bun-t2",
    ]
    assert bun[5] == "--preload"
    assert bun[6].endswith("/bun-t2.preload.mjs")
    assert bun[7:] == ["./tests/flow-seams.test.ts"]
    [pytest_call] = pytest_runs(calls)
    assert pytest_call[-9:] == [
        "-p",
        "guardrails.commands.adapters.pytest.plugin",
        "--omp-guard=user-data",
        "-m",
        "seam",
        "--cov",
        "--cov-append",
        "--cov-report=",
        "COVERAGE_FILE=/cov/python.coverage",
    ]


@pytest.mark.parametrize(
    "argv",
    [["t3"], ["t1", "--framework", "jest"], ["t1", "--coverage"], ["t1", "--surprise"]],
)
def test_test_tier_usage_errors_exit_two(checkout: Any, argv: list[str]) -> None:
    root, env = checkout
    code, output, calls = run(root, env, "scripts/run-test-tier.sh", *argv)
    assert code == 2
    assert "Usage:" in output
    assert calls == []


@pytest.mark.parametrize(
    ("staged", "bun_t2", "pytest_t2"),
    [
        ("orca-plugins/chat.css", False, False),
        ("tests/test_extra.py", False, True),
        ("tests/extra.test.ts", True, False),
        ("agent/extensions/new.ts", True, True),
    ],
)
def test_commit_tier_runs_the_t2_seams_a_staged_path_reaches(
    checkout: Any, staged: str, bun_t2: bool, pytest_t2: bool
) -> None:
    root, env = checkout
    git(root, env, "commit", "-qm", "base", "--no-verify")
    (root / staged).parent.mkdir(parents=True, exist_ok=True)
    (root / staged).write_text("x\n")
    git(root, env, "add", staged)
    code, output, calls = run(
        root, env, "scripts/run-ci-quality-gates.sh", "--tier", "commit"
    )
    assert code == 0, output
    assert (
        "T2 bun seams skipped: no staged path reaches them." not in output
    ) is bun_t2
    assert (
        "T2 pytest seams skipped: no staged path reaches them." not in output
    ) is pytest_t2
    assert any(call[-2:] == ["-m", "seam"] for call in pytest_runs(calls)) is pytest_t2
    assert (
        any("./tests/flow-seams.test.ts" in call for call in bun_runs(calls)) is bun_t2
    )


def audit_calls(calls: list[list[str]]) -> list[list[str]]:
    return [call for call in calls if call[-1].startswith("cwd=")]


def track(root: Path, env: dict[str, str], *paths: str) -> None:
    for path in paths:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("x\n")
    git(root, env, "add", *paths)


def test_dependency_audit_audits_every_discovered_lock_where_it_lives(
    checkout: Any,
) -> None:
    root, env = checkout
    track(
        root,
        env,
        "bun.lock",
        "uv.lock",
        "engine/uv.lock",
        "engine/requirements-pytest.lock",
        "tests/fixtures/frozen/bun.lock",
    )
    code, output, calls = run(root, env, "scripts/audit-dependencies.sh")
    assert code == 0, output
    real = root.resolve()
    assert sorted(audit_calls(calls)) == sorted([
        ["bun", "audit", f"cwd={real}"],
        [
            "uv",
            "audit",
            "--locked",
            "--preview-features",
            "audit-command",
            f"cwd={real}",
        ],
        [
            "uv",
            "audit",
            "--locked",
            "--preview-features",
            "audit-command",
            f"cwd={real / 'engine'}",
        ],
        [
            "uv",
            "run",
            "--locked",
            "pip-audit",
            "--requirement",
            "engine/requirements-pytest.lock",
            "--require-hashes",
            "--disable-pip",
            "--progress-spinner",
            "off",
            f"cwd={real}",
        ],
    ])
    assert "Dependency audit passed." in output


def test_one_failing_audit_fails_the_step_after_every_lock_is_audited(
    checkout: Any,
) -> None:
    root, env = checkout
    track(root, env, "bun.lock", "engine/uv.lock", "uv.lock")
    failing = f"cwd={(root / 'engine').resolve()}"
    code, output, calls = run(
        root, {**env, "FAKE_AUDIT_FAIL": failing}, "scripts/audit-dependencies.sh"
    )
    assert code == 1
    assert len(audit_calls(calls)) == 3
    assert "Dependency audit failed for: engine/uv.lock" in output


def test_a_lock_no_auditor_reads_fails_the_audit(checkout: Any) -> None:
    root, env = checkout
    track(root, env, "web/package-lock.json")
    code, output, _ = run(root, env, "scripts/audit-dependencies.sh")
    assert code == 1
    assert "No dependency auditor for web/package-lock.json." in output


@pytest.mark.parametrize("tier", ["commit", "ci"])
def test_both_gate_tiers_block_on_the_dependency_audit(
    checkout: Any, tier: str
) -> None:
    root, env = checkout
    track(root, env, "bun.lock")
    failing = f"cwd={root.resolve()}"
    code, output, calls = run(
        root,
        {**env, "FAKE_AUDIT_FAIL": failing},
        "scripts/run-ci-quality-gates.sh",
        "--tier",
        tier,
    )
    assert code == 1
    assert ["bun", "audit", failing] in calls
    assert "Dependency audit failed for: bun.lock" in output
    assert bun_runs(calls) == []
