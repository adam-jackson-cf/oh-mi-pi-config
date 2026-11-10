"""Device rollout classification without SSH, Infisical, omp or checkout side effects."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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
                 service_failed=False, malformed=False, pull_failed=False,
                 coverage="complete", husks="", find_output=None):
        self.stale = stale
        self.unreachable = unreachable
        self.dirty = dirty
        self.behind = behind
        self.service_failed = service_failed
        self.malformed = malformed
        self.pull_failed = pull_failed
        self.coverage = coverage
        self.husks = husks
        self.find_output = find_output
        self.commands = []

    def __call__(self, argv):
        self.commands.append(argv)
        text = " ".join(argv)
        remote = argv[0] == "ssh"
        code, stdout = 0, ""
        if remote and self.unreachable:
            code = 255
        elif "omp find" in text:
            code = int(self.coverage == "unavailable")
            stdout = (self.find_output if self.find_output is not None
                      else json.dumps({"coverage": self.coverage, "hits": []}))
        elif "by-request" in text:
            deleting = "rm -rf" in text
            stdout = "".join(f"{'deleted' if deleting and kind == 'husk' else kind} {name}\n"
                             for kind, name in (line.split() for line in self.husks.splitlines()))
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


class Pipeline:
    def __init__(self, codes=(0, 0)):
        self.codes = codes
        self.calls = []

    def __call__(self, producer, consumer):
        self.calls.append((producer, consumer))
        return self.codes


class DeviceRolloutTests(unittest.TestCase):
    def execute(self, runner, check=True, hostname="cdhvc2hw3l", pipeline=None):
        deployment = rollout.Rollout(HOSTS, check=check, runner=runner, home="/local",
                                    hostname=hostname, pipeline=pipeline or Pipeline())
        code = deployment.execute()
        return deployment, code

    def test_current_and_read_only_check(self):
        runner = Runner(husks="husk test-guidance\n")
        pipeline = Pipeline()
        deployment, code = self.execute(runner, pipeline=pipeline)
        self.assertEqual(pipeline.calls, [])
        self.assertFalse(any("rm -rf" in " ".join(cmd) for cmd in runner.commands))
        self.assertEqual(code, 1)
        agents = [row for row in deployment.rows if row[1] == "agents"]
        self.assertEqual({row[5] for row in agents}, {"husks-present"})
        runner = Runner()
        deployment, code = self.execute(runner)
        self.assertEqual(code, 0)
        self.assertEqual({row[4] for row in deployment.rows}, {"complete"})
        self.assertEqual(len(deployment.rows), 5)
        self.assertEqual({row[5] for row in deployment.rows}, {"current"})
        self.assertFalse(any("pull" in " ".join(cmd) or "systemctl" in " ".join(cmd)
                             for cmd in runner.commands))
        self.assertIn("device", deployment.report())
        self.assertFalse(any(row[:2] == ("mac-cf2", "omp") for row in deployment.rows))

    def test_local_label_resolves_normalized_hostname(self):
        deployment, code = self.execute(Runner(), hostname="CDHVC2HW3L.example.test")
        self.assertEqual(code, 0)
        self.assertEqual([row[0] for row in deployment.rows[:2]], ["mac-cf3"] * 2)

    def test_unknown_local_label_uses_short_hostname(self):
        deployment, code = self.execute(Runner(), hostname="OTHER-MAC.example.test")
        self.assertEqual(code, 0)
        self.assertEqual([row[0] for row in deployment.rows[:2]], ["other-mac"] * 2)

    def test_local_registry_host_is_not_contacted(self):
        runner = Runner()
        deployment, code = self.execute(runner, hostname="Adams-MacBook-Pro.local")
        self.assertEqual(code, 0)
        self.assertEqual([row[0] for row in deployment.rows[:2]], ["mac-cf2"] * 2)
        self.assertEqual(len(deployment.rows), 4)
        self.assertEqual([row[0] for row in deployment.rows[2:]],
                         ["codex-omarchy-nuc"] * 2)
        self.assertTrue(all(cmd[5] != "mac-cf2" for cmd in runner.commands if cmd[0] == "ssh"))

    def test_stale_is_nonzero(self):
        deployment, code = self.execute(Runner(stale=True))
        self.assertEqual(code, 1)
        self.assertEqual({row[5] for row in deployment.rows}, {"stale"})

    def test_unreachable_is_nonzero_and_does_not_crash(self):
        deployment, code = self.execute(Runner(unreachable=True))
        self.assertEqual(code, 1)
        self.assertEqual([row[5] for row in deployment.rows[2:]], ["unreachable"] * 3)
        self.assertEqual([row[4] for row in deployment.rows[2:]], ["unreachable"] * 3)
        self.assertNotIn("secret", deployment.report())

    def test_dirty_blocks_pull_in_both_modes(self):
        for check in (False, True):
            runner = Runner(dirty=True)
            deployment, code = self.execute(runner, check)
            self.assertEqual(code, 1)
            self.assertEqual(deployment.rows[2][5], "dirty-blocked")
            self.assertFalse(any("--ff-only" in " ".join(cmd) for cmd in runner.commands))
            self.assertTrue(any("--untracked-files=no" in " ".join(cmd) for cmd in runner.commands))

    def test_nuc_config_behind_is_nonzero_even_when_deployed_matches(self):
        deployment, code = self.execute(Runner(behind=True))
        self.assertEqual(code, 1)
        self.assertEqual(deployment.rows[-1][5], "nuc-config-behind")
        self.assertIn("nuc-config behind main by 3 commits: open an agent/nuc-sync-<date> PR",
                      deployment.report())

    def test_apply_pulls_only_remote_and_starts_service(self):
        runner = Runner(stale=True)
        deployment, code = self.execute(runner, check=False)
        self.assertEqual(code, 1)  # Local revisions are never pulled.
        pulls = [cmd for cmd in runner.commands if "--ff-only" in " ".join(cmd)]
        self.assertEqual(len(pulls), 2)
        self.assertTrue(all(cmd[0] == "ssh" for cmd in pulls))
        self.assertEqual([row[5] for row in deployment.rows[2:]], ["current"] * 3)
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
        self.assertEqual(deployment.rows[-1][5], "failed")
        self.assertIn("Active: failed", deployment.report())
        self.assertNotIn("secret", deployment.report())

    def test_malformed_deployment_is_failed(self):
        deployment, code = self.execute(Runner(malformed=True))
        self.assertEqual(code, 1)
        self.assertEqual(deployment.rows[-1][5], "failed")

    def test_failed_pull_is_failed(self):
        deployment, code = self.execute(Runner(pull_failed=True), check=False)
        self.assertEqual(code, 1)
        self.assertEqual(deployment.rows[2][5], "failed")

    def test_cli_exit_and_table_use_injected_runner(self):
        for runner, expected in ((Runner(), 0), (Runner(unreachable=True), 1)):
            pipeline = Pipeline()
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(rollout.main(["--check"], runner=runner, pipeline=pipeline),
                                 expected)
            self.assertIn("deployed", output.getvalue())
            self.assertIn("find", output.getvalue().splitlines()[0])
            self.assertEqual(pipeline.calls, [])

    def test_apply_streams_judge_key_to_every_device(self):
        pipeline = Pipeline()
        deployment, code = self.execute(Runner(), check=False, pipeline=pipeline)
        self.assertEqual(code, 0)
        self.assertEqual(len(pipeline.calls), 3)
        key = HOSTS["judgeKey"]
        for producer, consumer in pipeline.calls:
            self.assertEqual(producer[:7], ["infisical", "run", "--silent", "--projectId",
                                            key["infisicalProject"], "--env", key["environment"]])
            self.assertEqual(producer[-2:], [key["secret"], key["provider"]])
            self.assertTrue(consumer == ["omp", "auth", "import", "-"]
                            or (consumer[0] == "ssh" and "omp auth import -" in consumer[-1]))
        self.assertEqual([call[1][5] for call in pipeline.calls[1:]],
                         ["mac-cf2", "codex-omarchy-nuc"])
        self.assertTrue(pipeline.calls[2][1][6].startswith("bash -lc "))

    def test_key_failure_keeps_rows_non_current(self):
        deployment, code = self.execute(Runner(), check=False, pipeline=Pipeline((3, 1)))
        self.assertEqual(code, 1)
        self.assertEqual({row[4] for row in deployment.rows}, {"key-failed"})
        self.assertEqual({row[5] for row in deployment.rows}, {"find-blocked"})
        self.assertIn("judge key provisioning failed", deployment.report())

    def test_non_complete_find_keeps_rows_non_current(self):
        for coverage, output in (("partial", None), ("unavailable", None), ("failed", "not json"),
                                 ("failed", json.dumps({"coverage": "odd"}))):
            deployment, code = self.execute(Runner(coverage=coverage, find_output=output))
            self.assertEqual(code, 1)
            self.assertEqual({row[4] for row in deployment.rows}, {coverage})
            self.assertEqual({row[5] for row in deployment.rows}, {"find-blocked"})

    def test_find_canary_runs_on_configured_path(self):
        runner = Runner()
        self.execute(runner)
        finds = [" ".join(cmd) for cmd in runner.commands if "omp find" in " ".join(cmd)]
        self.assertEqual(len(finds), 3)
        self.assertIn("/local/.agents/scripts --json -q", finds[0])
        self.assertTrue(all("--json -q" in text for text in finds[1:]))

    def test_apply_deletes_only_husks_without_skill(self):
        runner = Runner(husks="husk coding-guidance\nskill test-guidance\n")
        deployment, code = self.execute(runner, check=False)
        self.assertEqual(code, 1)
        scripts = [" ".join(cmd) for cmd in runner.commands if "by-request" in " ".join(cmd)]
        self.assertEqual(len(scripts), 3)
        self.assertTrue(all("SKILL.md" in text and "rm -rf" in text for text in scripts))
        report = deployment.report()
        self.assertIn("mac-cf3: deleted by-request/coding-guidance husk", report)
        self.assertIn("mac-cf3: by-request/test-guidance holds a SKILL.md; not deleted", report)
        agents = [row for row in deployment.rows if row[1] == "agents"]
        self.assertEqual({row[5] for row in agents}, {"husks-present"})

    def test_husk_script_deletes_only_folders_without_skill(self):
        with tempfile.TemporaryDirectory() as home:
            base = Path(home) / ".agents/by-request"
            (base / "coding-guidance/__pycache__").mkdir(parents=True)
            (base / "test-guidance/nested").mkdir(parents=True)
            (base / "test-guidance/nested/SKILL.md").write_text("x")
            (base / "other").mkdir()
            deployment = rollout.Rollout({}, check=False, runner=rollout.run_command, home=home,
                                         hostname="test")
            self.assertEqual(deployment.husks("dev", Path(home) / ".agents"), "husks-present")
            self.assertEqual(sorted(path.name for path in base.iterdir()),
                             ["other", "test-guidance"])
            deployment.check = True
            self.assertEqual(deployment.husks("dev", Path(home) / ".agents"), "husks-present")
            (base / "test-guidance/nested/SKILL.md").unlink()
            self.assertEqual(deployment.husks("dev", Path(home) / ".agents"), "husks-present")
            self.assertTrue((base / "test-guidance").exists())

    def test_credential_builder_emits_auth_import_document(self):
        env = {"PATH": "/usr/bin:/bin", "FAKE_JUDGE_KEY": "fake-value"}
        result = subprocess.run([sys.executable, "-c", rollout.CREDENTIAL_BUILDER,
                                 "FAKE_JUDGE_KEY", "openrouter"],
                                capture_output=True, text=True, env=env, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"version": 1, "credentials": [
            {"provider": "openrouter", "credential": {"type": "api_key", "key": "fake-value"}}]})
        missing = subprocess.run([sys.executable, "-c", rollout.CREDENTIAL_BUILDER,
                                  "FAKE_JUDGE_KEY", "openrouter"],
                                 capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"},
                                 check=False)
        self.assertEqual((missing.returncode, missing.stdout), (3, ""))

    def test_pipeline_streams_stdout_to_stdin_without_output(self):
        with tempfile.TemporaryDirectory() as folder:
            sink = Path(folder) / "sink"
            codes = rollout.run_pipeline(
                [sys.executable, "-c", "print('payload')"],
                [sys.executable, "-c",
                 f"import sys; open({str(sink)!r}, 'w').write(sys.stdin.read())"])
            self.assertEqual(codes, (0, 0))
            self.assertEqual(sink.read_text(), "payload\n")


if __name__ == "__main__":
    unittest.main()
