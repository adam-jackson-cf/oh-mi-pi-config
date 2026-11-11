"""ci-runner.py against fake runner commands and a fake gh on PATH."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci-runner.py"
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


def status(context, state):
    return {"statuses": [{"context": context, "state": state}]}


def run(name, state, conclusion=None, run_id=1):
    return {"id": run_id, "name": name, "status": state, "conclusion": conclusion}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable))
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.config = self.root / "ci-runners.json"
        self.env = {**os.environ, "OMP_CI_RUNNERS": str(self.config), "FAKE_GH": str(self.root),
                    "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}

    def tearDown(self):
        self.folder.cleanup()

    def write_config(self, runners, timeout=60):
        """Write runners; each role gets `timeout` unless it sets one (None leaves it out)."""
        roles = {name: spec if "timeout" in spec or timeout is None else {**spec, "timeout": timeout}
                 for name, spec in runners.items()}
        self.config.write_text(json.dumps({"schemaVersion": 1, "runners": roles}))

    def responses(self, *pairs):
        (self.root / "responses.json").write_text(json.dumps(pairs))

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                              env=self.env, check=False, timeout=30)

    def recorder(self, code=0):
        """A runner command that records its argv and exits with `code`."""
        record = self.root / "argv.json"
        return [sys.executable, "-c",
                f"import json, sys; json.dump(sys.argv[1:], open({str(record)!r}, 'w')); sys.exit({code})",
                "{repo}", "sha={sha}", "--timeout", "{timeout}", "--tail", "{tail}", "{other}"], record


class CiRunnerTests(Fixture):
    def test_configured_command_substitutes_placeholders_and_passes_exit_code(self):
        argv, record = self.recorder(code=7)
        self.write_config({"fake": {"submit": argv, "wait": argv, "status": argv, "logs": argv}})
        result = self.cli("wait", "fake", REPO, SHA, "--timeout", "90", "--tail", "15")
        self.assertEqual(result.returncode, 7)
        self.assertEqual(json.loads(record.read_text()),
                         [REPO, "sha=" + SHA, "--timeout", "90", "--tail", "15", "{other}"])

    def test_role_timeout_is_the_default_wait_limit(self):
        argv, record = self.recorder()
        self.write_config({"fake": {"submit": argv, "wait": argv, "status": argv, "logs": argv}},
                          timeout=75)
        self.assertEqual(self.cli("wait", "fake", REPO, SHA).returncode, 0)
        self.assertEqual(json.loads(record.read_text())[2:4], ["--timeout", "75"])

    def test_role_without_positive_timeout_is_a_config_error(self):
        argv, record = self.recorder()
        for timeout in (None, 0, "60", True):
            spec = {"submit": argv, "wait": argv, "status": argv, "logs": argv}
            if timeout is not None:
                spec["timeout"] = timeout
            self.write_config({"fake": spec}, timeout=None)
            result = self.cli("wait", "fake", REPO, SHA, "--timeout", "5")
            self.assertEqual(result.returncode, 2, timeout)
            self.assertIn("no positive timeout", result.stderr)
        self.assertFalse(record.exists())

    def test_missing_config_and_role_exit_2_with_rollout_hint(self):
        result = self.cli("submit", "ci-gate", REPO, SHA)
        self.assertEqual(result.returncode, 2)
        self.assertIn("scripts/device-rollout.py to write ci-runners.json", result.stderr)
        self.write_config({"github-actions": {"submit": None, "wait": None, "status": None,
                                              "logs": None}})
        result = self.cli("submit", "ci-gate", REPO, SHA)
        self.assertEqual(result.returncode, 2)
        self.assertIn("'ci-gate' is not configured", result.stderr)
        self.assertIn("run python3 ", result.stderr)

    def test_invalid_role_command_is_a_config_error(self):
        self.write_config({"bad": {"submit": "not argv", "wait": None, "status": None, "logs": None}})
        self.assertEqual(self.cli("submit", "bad", REPO, SHA).returncode, 2)
        self.write_config({"bad": {"submit": None, "wait": None, "status": None}})
        self.assertEqual(self.cli("submit", "bad", REPO, SHA).returncode, 2)

    def test_null_submit_starts_on_push(self):
        self.write_config({"github-actions": {"submit": None, "wait": None, "status": None,
                                              "logs": None}})
        result = self.cli("submit", "github-actions", REPO, SHA)
        self.assertEqual(result.returncode, 0)
        self.assertIn("starts on push", result.stdout)
        self.assertFalse((self.root / "calls").exists())

    def test_rejects_unsafe_repo_and_sha_before_substitution(self):
        argv, record = self.recorder()
        self.write_config({"fake": {"submit": argv, "wait": argv, "status": argv, "logs": argv}})
        self.assertEqual(self.cli("submit", "fake", "owner/repo;rm", SHA).returncode, 2)
        self.assertEqual(self.cli("submit", "fake", REPO, "HEAD").returncode, 2)
        self.assertFalse(record.exists())


class BuiltInPollerTests(Fixture):
    def setUp(self):
        super().setUp()
        self.write_config({"github-actions": {"submit": None, "wait": None, "status": None,
                                              "logs": None}})

    def wait(self, *extra):
        context = () if "--context" in extra else ("--context", "gate")
        return self.cli("wait", "github-actions", REPO, SHA, "--interval", "0.05", *context, *extra)

    def test_built_in_poller_requires_the_declared_context(self):
        for operation in ("wait", "status"):
            result = self.cli(operation, "github-actions", REPO, SHA)
            self.assertEqual(result.returncode, 2)
            self.assertIn("pass --context", result.stderr)
        self.assertFalse((self.root / "calls").exists())

    def test_status_context_success_after_pending(self):
        self.responses((status("gate", "pending"), []), (status("gate", "success"), []))
        result = self.wait()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["state"], "success")
        calls = (self.root / "calls").read_text().splitlines()
        self.assertIn(f"api repos/{REPO}/commits/{SHA}/status", calls)
        self.assertIn(f"api repos/{REPO}/commits/{SHA}/check-runs?per_page=100", calls)

    def test_check_run_success_uses_named_context(self):
        self.responses(({"statuses": []}, [run("gate", "completed", "failure", 1),
                                           run("build", "completed", "success", 2),
                                           run("gate", "completed", "success", 3)]))
        result = self.wait("--context", "gate")
        self.assertEqual(result.returncode, 0)
        result = self.wait("--context", "build")
        self.assertEqual(result.returncode, 0)

    def test_failure_and_error_states_fail(self):
        for pair in ((status("gate", "failure"), []), (status("gate", "error"), []),
                     ({"statuses": []}, [run("gate", "completed", "cancelled")]),
                     (status("gate", "success"), [run("gate", "completed", "failure")])):
            self.responses(pair)
            (self.root / "calls").unlink(missing_ok=True)
            result = self.wait()
            self.assertEqual(result.returncode, 1, pair)
            self.assertEqual(json.loads(result.stdout)["state"], "failure")

    def test_timeout_exits_124(self):
        self.responses((status("other", "success"), [run("gate", "in_progress")]))
        result = self.wait("--timeout", "0.2")
        self.assertEqual(result.returncode, 124)
        self.assertIn("Timed out", result.stderr)

    def test_status_reports_once(self):
        self.responses((status("gate", "pending"), []))
        result = self.cli("status", "github-actions", REPO, SHA, "--context", "gate")
        self.assertEqual(result.returncode, 3)
        self.assertEqual(json.loads(result.stdout)["state"], "pending")
        self.responses((status("gate", "success"), []))
        self.assertEqual(self.cli("status", "github-actions", REPO, SHA, "--context", "gate").returncode, 0)

    def test_gh_failure_is_error_without_echoing_output(self):
        (self.root / "exit").write_text("4")
        result = self.wait()
        self.assertEqual(result.returncode, 1)
        self.assertIn("failed with exit code 4", result.stderr)

    def test_null_logs_is_config_error(self):
        self.assertEqual(self.cli("logs", "github-actions", REPO, SHA).returncode, 2)


if __name__ == "__main__":
    unittest.main()
