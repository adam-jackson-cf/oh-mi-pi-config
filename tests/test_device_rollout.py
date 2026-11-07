"""Device rollout classification without SSH or checkout side effects."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import unittest
from contextlib import redirect_stdout

MODULE = Path(__file__).resolve().parents[1] / "scripts/device-rollout.py"
spec = importlib.util.spec_from_file_location("device_rollout", MODULE)
assert spec is not None and spec.loader is not None
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)
HOSTS = json.loads(MODULE.with_name("device-rollout-hosts.json").read_text())
CURRENT = "a" * 40
OLD = "b" * 40


class Runner:
    def __init__(self, stale=False, unreachable=False, dirty=False, behind=False,
                 service_failed=False, malformed=False, pull_failed=False):
        self.stale = stale
        self.unreachable = unreachable
        self.dirty = dirty
        self.behind = behind
        self.service_failed = service_failed
        self.malformed = malformed
        self.pull_failed = pull_failed
        self.commands = []

    def __call__(self, argv):
        self.commands.append(argv)
        text = " ".join(argv)
        remote = argv[0] == "ssh"
        code, stdout = 0, ""
        if remote and self.unreachable:
            code = 255
        elif "merge-base" in text:
            code = int(self.behind)
        elif "rev-list" in text:
            stdout = "3\n"
        elif "rev-parse" in text:
            stdout = (OLD if "HEAD" in text and self.stale else CURRENT) + "\n"
        elif "--porcelain" in text:
            stdout = " M tracked\n" if self.dirty else ""
        elif "pull" in text and "--ff-only" in text:
            code = int(self.pull_failed)
            if not self.pull_failed:
                self.stale = False
        elif "systemctl" in text and "start" in text:
            code = int(self.service_failed)
        elif "systemctl" in text and "status" in text:
            code, stdout = 3, "Active: failed (Result: exit-code)\nsecret log not displayed\n"
        elif "cat" in text:
            stdout = "{}" if self.malformed else json.dumps({"revision": OLD if self.stale else CURRENT})
        return subprocess.CompletedProcess(argv, code, stdout, "secret error not displayed")


class DeviceRolloutTests(unittest.TestCase):
    def execute(self, runner, check=True):
        deployment = rollout.Rollout(HOSTS, check=check, runner=runner, home="/local")
        code = deployment.execute()
        return deployment, code

    def test_current_and_read_only_check(self):
        runner = Runner()
        deployment, code = self.execute(runner)
        self.assertEqual(code, 0)
        self.assertEqual(len(deployment.rows), 5)
        self.assertEqual({row[4] for row in deployment.rows}, {"current"})
        self.assertFalse(any("pull" in " ".join(cmd) or "systemctl" in " ".join(cmd)
                             for cmd in runner.commands))
        self.assertIn("device", deployment.report())
        self.assertFalse(any(row[:2] == ("mac-cf2", "omp") for row in deployment.rows))

    def test_stale_is_nonzero(self):
        deployment, code = self.execute(Runner(stale=True))
        self.assertEqual(code, 1)
        self.assertEqual({row[4] for row in deployment.rows}, {"stale"})

    def test_unreachable_is_nonzero_and_does_not_crash(self):
        deployment, code = self.execute(Runner(unreachable=True))
        self.assertEqual(code, 1)
        self.assertEqual([row[4] for row in deployment.rows[2:]], ["unreachable"] * 3)
        self.assertNotIn("secret", deployment.report())

    def test_dirty_blocks_pull_in_both_modes(self):
        for check in (False, True):
            runner = Runner(dirty=True)
            deployment, code = self.execute(runner, check)
            self.assertEqual(code, 1)
            self.assertEqual(deployment.rows[2][4], "dirty-blocked")
            self.assertFalse(any("--ff-only" in " ".join(cmd) for cmd in runner.commands))
            self.assertTrue(any("--untracked-files=no" in " ".join(cmd) for cmd in runner.commands))

    def test_nuc_config_behind_is_nonzero_even_when_deployed_matches(self):
        deployment, code = self.execute(Runner(behind=True))
        self.assertEqual(code, 1)
        self.assertEqual(deployment.rows[-1][4], "nuc-config-behind")
        self.assertIn("nuc-config behind main by 3 commits: open an agent/nuc-sync-<date> PR",
                      deployment.report())

    def test_apply_pulls_only_remote_and_starts_service(self):
        runner = Runner(stale=True)
        deployment, code = self.execute(runner, check=False)
        self.assertEqual(code, 1)  # Local revisions are never pulled.
        pulls = [cmd for cmd in runner.commands if "--ff-only" in " ".join(cmd)]
        self.assertEqual(len(pulls), 2)
        self.assertTrue(all(cmd[0] == "ssh" for cmd in pulls))
        self.assertEqual([row[4] for row in deployment.rows[2:]], ["current"] * 3)
        self.assertTrue(any("systemctl --user start omp-config-pull.service" in " ".join(cmd)
                            for cmd in runner.commands))
        for cmd in runner.commands:
            if cmd[0] == "ssh":
                self.assertEqual(cmd[1:5], ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
                if cmd[5] == "codex-omarchy-nuc":
                    self.assertTrue(cmd[6].startswith("bash -lc "))
                    self.assertNotIn("git -C", cmd[6].replace(".agents", "") if ".omp" in cmd[6] else "")

    def test_service_failure_reports_safe_status(self):
        runner = Runner(service_failed=True)
        deployment, code = self.execute(runner, check=False)
        self.assertEqual(code, 1)
        self.assertEqual(deployment.rows[-1][4], "failed")
        self.assertIn("Active: failed", deployment.report())
        self.assertNotIn("secret", deployment.report())

    def test_malformed_deployment_is_failed(self):
        deployment, code = self.execute(Runner(malformed=True))
        self.assertEqual(code, 1)
        self.assertEqual(deployment.rows[-1][4], "failed")

    def test_failed_pull_is_failed(self):
        deployment, code = self.execute(Runner(pull_failed=True), check=False)
        self.assertEqual(code, 1)
        self.assertEqual(deployment.rows[2][4], "failed")

    def test_cli_exit_and_table_use_injected_runner(self):
        for runner, expected in ((Runner(), 0), (Runner(unreachable=True), 1)):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(rollout.main(["--check"], runner=runner), expected)
            self.assertIn("deployed", output.getvalue())


if __name__ == "__main__":
    unittest.main()
