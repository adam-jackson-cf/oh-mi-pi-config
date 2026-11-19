"""ci_runner.py against fake runner commands and a fake gh on PATH, plus the runner parity check."""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PROCESS_TIMEOUT = 60
SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci_runner.py"
REPO = "owner/repo"
SHA = "a" * 40
# Fake gh: answers `gh api <path>` from $FAKE_GH/<n>.json responses, advancing one
# response per status call; the last response repeats. Exit code from $FAKE_GH/exit.
FAKE_GH = """#!{python}
import json, os, pathlib, sys
root = pathlib.Path(os.environ["FAKE_GH"])
with (root / "calls").open("a") as stream:
    stream.write(" ".join(sys.argv[1:]) + "\\n")
if (root / "exit").exists():
    sys.exit(int((root / "exit").read_text()))
responses = json.loads((root / "responses.json").read_text())
calls = (root / "calls").read_text().splitlines()
index = min(sum("/status" in line for line in calls) - 1, len(responses) - 1)
status, runs = responses[index]
print(json.dumps(status if sys.argv[2].endswith("/status") else {{"check_runs": runs}}))
"""


def status(context: str, state: str) -> dict[str, Any]:
    return {"statuses": [{"context": context, "state": state}]}


def run(
    name: str, state: str, conclusion: str | None = None, run_id: int = 1
) -> dict[str, Any]:
    return {"id": run_id, "name": name, "status": state, "conclusion": conclusion}


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable))
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.config = self.root / "ci-runners.json"
        self.env = {
            **os.environ,
            "OMP_CI_RUNNERS": str(self.config),
            "FAKE_GH": str(self.root),
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        }

    def tearDown(self) -> None:
        self.folder.cleanup()

    def write_config(
        self, runners: dict[str, dict[str, Any]], timeout: object = 60
    ) -> None:
        """Write runners; each role gets `timeout` unless it sets one (None leaves it out)."""
        roles = {
            name: spec
            if "timeout" in spec or timeout is None
            else {**spec, "timeout": timeout}
            for name, spec in runners.items()
        }
        self.config.write_text(json.dumps({"schemaVersion": 1, "runners": roles}))

    def responses(self, *pairs: tuple[dict[str, Any], list[dict[str, Any]]]) -> None:
        (self.root / "responses.json").write_text(json.dumps(pairs))

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
            timeout=30,
        )

    def recorder(self, code: int = 0) -> tuple[list[str], Path]:
        """A runner command that records its argv and exits with `code`."""
        record = self.root / "argv.json"
        return [
            sys.executable,
            "-c",
            f"import json, sys; json.dump(sys.argv[1:], open({str(record)!r}, 'w')); sys.exit({code})",
            "{repo}",
            "sha={sha}",
            "--timeout",
            "{timeout}",
            "--tail",
            "{tail}",
            "{other}",
        ], record


class CiRunnerTests(Fixture):
    def test_configured_command_substitutes_placeholders_and_passes_exit_code(
        self,
    ) -> None:
        argv, record = self.recorder(code=7)
        self.write_config({
            "fake": {"submit": argv, "wait": argv, "status": argv, "logs": argv}
        })
        result = self.cli("wait", "fake", REPO, SHA, "--timeout", "90", "--tail", "15")
        assert result.returncode == 7
        assert json.loads(record.read_text()) == [
            REPO,
            "sha=" + SHA,
            "--timeout",
            "90",
            "--tail",
            "15",
            "{other}",
        ]

    def test_role_timeout_is_the_default_wait_limit(self) -> None:
        argv, record = self.recorder()
        self.write_config(
            {"fake": {"submit": argv, "wait": argv, "status": argv, "logs": argv}},
            timeout=75,
        )
        assert self.cli("wait", "fake", REPO, SHA).returncode == 0
        assert json.loads(record.read_text())[2:4] == ["--timeout", "75"]

    def test_role_without_positive_timeout_is_a_config_error(self) -> None:
        argv, record = self.recorder()
        for timeout in (None, 0, "60", True):
            spec: dict[str, object] = {
                "submit": argv,
                "wait": argv,
                "status": argv,
                "logs": argv,
            }
            if timeout is not None:
                spec["timeout"] = timeout
            self.write_config({"fake": spec}, timeout=None)
            result = self.cli("wait", "fake", REPO, SHA, "--timeout", "5")
            assert result.returncode == 2, timeout
            assert "no positive timeout" in result.stderr
        assert not record.exists()

    def test_missing_config_and_role_exit_2_with_rollout_hint(self) -> None:
        result = self.cli("submit", "ci-gate", REPO, SHA)
        assert result.returncode == 2
        assert "scripts/device_rollout.py to write ci-runners.json" in result.stderr
        self.write_config({
            "github-actions": {
                "submit": None,
                "wait": None,
                "status": None,
                "logs": None,
            }
        })
        result = self.cli("submit", "ci-gate", REPO, SHA)
        assert result.returncode == 2
        assert "'ci-gate' is not configured" in result.stderr
        assert "run python3 " in result.stderr

    def test_invalid_role_command_is_a_config_error(self) -> None:
        self.write_config({
            "bad": {"submit": "not argv", "wait": None, "status": None, "logs": None}
        })
        assert self.cli("submit", "bad", REPO, SHA).returncode == 2
        self.write_config({"bad": {"submit": None, "wait": None, "status": None}})
        assert self.cli("submit", "bad", REPO, SHA).returncode == 2

    def test_null_submit_starts_on_push(self) -> None:
        self.write_config({
            "github-actions": {
                "submit": None,
                "wait": None,
                "status": None,
                "logs": None,
            }
        })
        result = self.cli("submit", "github-actions", REPO, SHA)
        assert result.returncode == 0
        assert "starts on push" in result.stdout
        assert not (self.root / "calls").exists()

    def test_rejects_unsafe_repo_and_sha_before_substitution(self) -> None:
        argv, record = self.recorder()
        self.write_config({
            "fake": {"submit": argv, "wait": argv, "status": argv, "logs": argv}
        })
        assert self.cli("submit", "fake", "owner/repo;rm", SHA).returncode == 2
        assert self.cli("submit", "fake", REPO, "HEAD").returncode == 2
        assert not record.exists()


class BuiltInPollerTests(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.write_config({
            "github-actions": {
                "submit": None,
                "wait": None,
                "status": None,
                "logs": None,
            }
        })

    def wait(self, *extra: str) -> subprocess.CompletedProcess[str]:
        context = () if "--context" in extra else ("--context", "gate")
        return self.cli(
            "wait", "github-actions", REPO, SHA, "--interval", "0.05", *context, *extra
        )

    def test_built_in_poller_requires_the_declared_context(self) -> None:
        for operation in ("wait", "status"):
            result = self.cli(operation, "github-actions", REPO, SHA)
            assert result.returncode == 2
            assert "pass --context" in result.stderr
        assert not (self.root / "calls").exists()

    def test_status_context_success_after_pending(self) -> None:
        self.responses((status("gate", "pending"), []), (status("gate", "success"), []))
        result = self.wait()
        assert result.returncode == 0
        assert json.loads(result.stdout)["state"] == "success"
        calls = (self.root / "calls").read_text().splitlines()
        assert f"api repos/{REPO}/commits/{SHA}/status" in calls
        assert f"api repos/{REPO}/commits/{SHA}/check-runs?per_page=100" in calls

    def test_check_run_success_uses_named_context(self) -> None:
        self.responses((
            {"statuses": []},
            [
                run("gate", "completed", "failure", 1),
                run("build", "completed", "success", 2),
                run("gate", "completed", "success", 3),
            ],
        ))
        result = self.wait("--context", "gate")
        assert result.returncode == 0
        result = self.wait("--context", "build")
        assert result.returncode == 0

    def test_failure_and_error_states_fail(self) -> None:
        pairs: tuple[tuple[dict[str, Any], list[dict[str, Any]]], ...] = (
            (status("gate", "failure"), []),
            (status("gate", "error"), []),
            ({"statuses": []}, [run("gate", "completed", "cancelled")]),
            (status("gate", "success"), [run("gate", "completed", "failure")]),
        )
        for pair in pairs:
            self.responses(pair)
            (self.root / "calls").unlink(missing_ok=True)
            result = self.wait()
            assert result.returncode == 1, pair
            assert json.loads(result.stdout)["state"] == "failure"

    def test_timeout_exits_124(self) -> None:
        self.responses((status("other", "success"), [run("gate", "in_progress")]))
        result = self.wait("--timeout", "0.2")
        assert result.returncode == 124
        assert "Timed out" in result.stderr

    def test_status_reports_once(self) -> None:
        self.responses((status("gate", "pending"), []))
        result = self.cli("status", "github-actions", REPO, SHA, "--context", "gate")
        assert result.returncode == 3
        assert json.loads(result.stdout)["state"] == "pending"
        self.responses((status("gate", "success"), []))
        assert (
            self.cli(
                "status", "github-actions", REPO, SHA, "--context", "gate"
            ).returncode
            == 0
        )

    def test_gh_failure_is_error_without_echoing_output(self) -> None:
        (self.root / "exit").write_text("4")
        result = self.wait()
        assert result.returncode == 1
        assert "failed with exit code 4" in result.stderr

    def test_null_logs_is_config_error(self) -> None:
        assert self.cli("logs", "github-actions", REPO, SHA).returncode == 2


GATES_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run-ci-quality-gates.sh"
# Stops the run right after the parity check, before any lint, type or test tool starts.
STOP = "#!/bin/sh\nexit 42\n"


class RunnerParityTests(unittest.TestCase):
    """run-ci-quality-gates.sh parity: the tracked pre-commit hook must use this runner."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="ci-parity-test-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        (self.repo / "scripts").mkdir(parents=True)
        (self.repo / ".githooks").mkdir()
        shutil.copy(GATES_SCRIPT, self.repo / "scripts")
        subprocess.run(
            ["git", "init", "-q", str(self.repo)], check=True, timeout=PROCESS_TIMEOUT
        )
        # macOS bash 3.2 treats an empty array as unbound under `set -u`: track one Markdown file.
        (self.repo / "readme.md").write_text("# fixture\n")
        subprocess.run(
            ["git", "-C", str(self.repo), "add", "readme.md"],
            check=True,
            timeout=PROCESS_TIMEOUT,
        )
        binary = Path(scratch.name) / "bin"
        binary.mkdir()
        for name in ("bunx", "bun"):
            (binary / name).write_text(STOP)
            (binary / name).chmod(0o755)
        self.env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.env["PATH"] = str(binary) + os.pathsep + self.env["PATH"]

    def hook(self, command: str) -> None:
        (self.repo / ".githooks/pre-commit").write_text(
            f"#!/usr/bin/env bash\nexec {command}\n"
        )

    def run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.repo / "scripts/run-ci-quality-gates.sh")],
            cwd=self.repo,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_hook_using_the_runner_satisfies_parity_without_a_workflow(self) -> None:
        self.hook("bash scripts/run-ci-quality-gates.sh --tier commit --fix --stage")
        result = self.run_script()
        assert "Runner parity check passed." in result.stdout
        assert result.returncode == 42, "the run must continue to the quality tools"
        assert not (self.repo / ".github").exists()

    def test_hook_without_runner_fails_even_when_workflow_references_it(self) -> None:
        self.hook("make check")
        workflow = self.repo / ".github/workflows/ci-quality-gates.yml"
        workflow.parent.mkdir(parents=True)
        workflow.write_text("steps:\n  - run: bash scripts/run-ci-quality-gates.sh\n")
        result = self.run_script()
        assert result.returncode == 1
        assert (
            "Runner parity check failed: .githooks/pre-commit must reference"
            in result.stderr
        )

    def test_missing_hook_fails(self) -> None:
        result = self.run_script()
        assert result.returncode == 1
        assert ".githooks/pre-commit" in result.stderr


if __name__ == "__main__":
    unittest.main()
