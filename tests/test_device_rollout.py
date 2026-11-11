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
                 coverage="complete", husks="", find_output=None, runners="current"):
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
        self.runners = runners
        self.commands = []

    def __call__(self, argv):
        self.commands.append(argv)
        text = " ".join(argv)
        remote = argv[0] == "ssh"
        code, stdout = 0, ""
        if remote and self.unreachable:
            code = 255
        elif "ci-runners.json" in text:
            stdout = self.runners + "\n"
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


def row(deployment, device, component):
    return next(item for item in deployment.rows if item[:2] == [device, component])


def checkouts(deployment, skip=()):
    """Checkout and deployment rows, without the ci-runners rows or skipped devices."""
    return [item for item in deployment.rows if item[1] != "ci-runners" and item[0] not in skip]


class DeviceRolloutTests(unittest.TestCase):
    def execute(self, runner, check=True, hostname="cdhvc2hw3l", pipeline=None):
        deployment = rollout.Rollout(HOSTS, check=check, runner=runner, home="/local",
                                    hostname=hostname, pipeline=pipeline or Pipeline(), base="/base")
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
        self.assertEqual({row[4] for row in checkouts(deployment)}, {"complete"})
        self.assertEqual(len(deployment.rows), 8)
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
        self.assertEqual(len(deployment.rows), 6)
        self.assertEqual([row[:2] for row in deployment.rows[2:]],
                         [["mac-cf2", "ci-runners"], ["codex-omarchy-nuc", "agents"],
                          ["codex-omarchy-nuc", "omp"], ["codex-omarchy-nuc", "ci-runners"]])
        self.assertTrue(all(cmd[5] != "mac-cf2" for cmd in runner.commands if cmd[0] == "ssh"))

    def test_stale_is_nonzero(self):
        deployment, code = self.execute(Runner(stale=True))
        self.assertEqual(code, 1)
        self.assertEqual({row[5] for row in checkouts(deployment)}, {"stale"})

    def test_unreachable_is_nonzero_and_does_not_crash(self):
        deployment, code = self.execute(Runner(unreachable=True))
        self.assertEqual(code, 1)
        remote = deployment.rows[3:]
        self.assertEqual([row[5] for row in remote], ["unreachable"] * 5)
        self.assertEqual([row[4] for row in checkouts(deployment, ("mac-cf3",))],
                         ["unreachable"] * 3)
        self.assertEqual(row(deployment, "mac-cf3", "ci-runners")[5], "current")
        self.assertNotIn("secret", deployment.report())

    def test_dirty_blocks_pull_in_both_modes(self):
        for check in (False, True):
            runner = Runner(dirty=True)
            deployment, code = self.execute(runner, check)
            self.assertEqual(code, 1)
            self.assertEqual(row(deployment, "mac-cf2", "agents")[5], "dirty-blocked")
            self.assertFalse(any("--ff-only" in " ".join(cmd) for cmd in runner.commands))
            self.assertTrue(any("--untracked-files=no" in " ".join(cmd) for cmd in runner.commands))

    def test_nuc_config_behind_is_nonzero_even_when_deployed_matches(self):
        deployment, code = self.execute(Runner(behind=True))
        self.assertEqual(code, 1)
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "omp")[5], "nuc-config-behind")
        self.assertIn("nuc-config behind main by 3 commits: open an agent/nuc-sync-<date> PR",
                      deployment.report())

    def test_apply_pulls_only_remote_and_starts_service(self):
        runner = Runner(stale=True)
        deployment, code = self.execute(runner, check=False)
        self.assertEqual(code, 1)  # Local revisions are never pulled.
        pulls = [cmd for cmd in runner.commands if "--ff-only" in " ".join(cmd)]
        self.assertEqual(len(pulls), 2)
        self.assertTrue(all(cmd[0] == "ssh" for cmd in pulls))
        self.assertEqual([row[5] for row in checkouts(deployment, ("mac-cf3",))], ["current"] * 3)
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
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "omp")[5], "failed")
        self.assertIn("Active: failed", deployment.report())
        self.assertNotIn("secret", deployment.report())

    def test_malformed_deployment_is_failed(self):
        deployment, code = self.execute(Runner(malformed=True))
        self.assertEqual(code, 1)
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "omp")[5], "failed")

    def test_failed_pull_is_failed(self):
        deployment, code = self.execute(Runner(pull_failed=True), check=False)
        self.assertEqual(code, 1)
        self.assertEqual(row(deployment, "mac-cf2", "agents")[5], "failed")

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
        self.assertEqual({row[4] for row in checkouts(deployment)}, {"key-failed"})
        self.assertEqual({row[5] for row in checkouts(deployment)}, {"find-blocked"})
        self.assertIn("judge key provisioning failed", deployment.report())

    def test_non_complete_find_keeps_rows_non_current(self):
        for coverage, output in (("partial", None), ("unavailable", None), ("failed", "not json"),
                                 ("failed", json.dumps({"coverage": "odd"}))):
            deployment, code = self.execute(Runner(coverage=coverage, find_output=output))
            self.assertEqual(code, 1)
            self.assertEqual({row[4] for row in checkouts(deployment)}, {coverage})
            self.assertEqual({row[5] for row in checkouts(deployment)}, {"find-blocked"})

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

    def test_ci_runners_row_per_device_and_check_is_read_only(self):
        for state in ("missing", "stale"):
            runner = Runner(runners=state)
            deployment, code = self.execute(runner)
            self.assertEqual(code, 1)
            rows = [item for item in deployment.rows if item[1] == "ci-runners"]
            self.assertEqual([item[0] for item in rows], ["mac-cf3", "mac-cf2", "codex-omarchy-nuc"])
            self.assertEqual({item[5] for item in rows}, {state})
            self.assertIn(f"mac-cf2: ci-runners.json {state}; run device-rollout.py without --check",
                          deployment.report())
            writes = [cmd for cmd in runner.commands if "ci-runners.json" in " ".join(cmd)]
            self.assertEqual(len(writes), 3)
            self.assertTrue(all(" check " in " ".join(cmd) and " apply " not in " ".join(cmd)
                                for cmd in writes))
        runner = Runner(runners="written")
        deployment, code = self.execute(runner, check=False)
        self.assertEqual(code, 0)
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "ci-runners")[5], "current")
        local, mac, nuc = [cmd for cmd in runner.commands if "ci-runners.json" in " ".join(cmd)]
        self.assertEqual(local[0], sys.executable)
        self.assertEqual(local[4], "/base/ci-runners.json")
        self.assertEqual(mac[:6], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "mac-cf2"])
        self.assertIn("python3 -c", mac[6])
        self.assertIn("~/.omp/ci-runners.json", mac[6])
        self.assertTrue(nuc[6].startswith("bash -lc "))

    def test_ci_runners_failure_is_nonzero(self):
        deployment, code = self.execute(Runner(runners="Traceback"))
        self.assertEqual(code, 1)
        self.assertEqual(row(deployment, "mac-cf3", "ci-runners")[5], "failed")

    def test_runner_document_local_on_ci_gate_host_and_ssh_elsewhere(self):
        definition = HOSTS["ciRunners"]
        host = definition["roles"]["ci-gate"]["host"]
        on_host = rollout.runner_document(definition, host, "~/.omp")["runners"]
        self.assertEqual(on_host["ci-gate"]["wait"],
                         ["python3", "~/.omp/scripts/ci-gate.py", "wait", "{repo}", "{sha}",
                          "--timeout", "{timeout}"])
        elsewhere = rollout.runner_document(definition, "mac-cf3", "/base")["runners"]
        self.assertEqual(elsewhere["ci-gate"]["submit"],
                         ["ssh", "-o", "BatchMode=yes", host,
                          'bash -lc "python3 ~/.omp/scripts/ci-gate.py submit {repo} {sha}"'])
        self.assertEqual(elsewhere["ci-gate"]["logs"][-1],
                         'bash -lc "python3 ~/.omp/scripts/ci-gate.py logs {repo} {sha} --tail {tail}"')
        for runners in (on_host, elsewhere):
            actions = runners["github-actions"]
            self.assertEqual([actions[name] for name in ("submit", "wait", "status")], [None] * 3)
            self.assertIn("--log-failed", actions["logs"][2])
            self.assertEqual(actions["logs"][-3:], ["{repo}", "{sha}", "{tail}"])
        for bad in ({**definition, "remoteBase": "/abs; rm"},
                    {**definition, "roles": {"x": {"kind": "ci-gate", "host": "a b"}}},
                    {**definition, "roles": {"x": {"kind": "other"}}}):
            with self.assertRaises(ValueError):
                rollout.runner_document(bad, "mac-cf3", "/base")

    def test_runners_writer_is_idempotent_and_feeds_ci_runner(self):
        with tempfile.TemporaryDirectory() as base:
            deployment = rollout.Rollout({"ciRunners": HOSTS["ciRunners"]}, check=True,
                                         runner=rollout.run_command, home=base, hostname="dev",
                                         base=base)
            target = Path(base) / "ci-runners.json"
            deployment.runners("dev", 0)
            self.assertEqual(deployment.rows[-1], ["dev", "ci-runners", "-", "-", "-", "missing"])
            self.assertFalse(target.exists())
            deployment.check = False
            deployment.runners("dev", 0)
            written = target.read_text()
            mtime = target.stat().st_mtime_ns
            deployment.runners("dev", 0)
            deployment.check = True
            deployment.runners("dev", 0)
            self.assertEqual([item[5] for item in deployment.rows[1:]], ["current"] * 3)
            self.assertEqual(target.read_text(), written)
            self.assertEqual(target.stat().st_mtime_ns, mtime)
            document = json.loads(written)
            self.assertEqual(document["schemaVersion"], 1)
            self.assertEqual(sorted(document["runners"]), ["ci-gate", "github-actions"])
            target.write_text("{}")
            deployment.runners("dev", 0)
            self.assertEqual(deployment.rows[-1][5], "stale")
            self.assertEqual(target.read_text(), "{}")
            deployment.check = False
            deployment.runners("dev", 0)
            self.assertEqual(target.read_text(), written)
            cli = MODULE.with_name("ci-runner.py")
            result = subprocess.run([sys.executable, str(cli), "submit", "github-actions", "o/r",
                                     "a" * 40], capture_output=True, text=True, check=False,
                                    env={"PATH": "/usr/bin:/bin", "OMP_CI_RUNNERS": str(target)})
            self.assertEqual(result.returncode, 0)
            self.assertIn("starts on push", result.stdout)

    def test_writer_expands_home_paths_on_target(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home, ".omp").mkdir()
            document = json.dumps(rollout.runner_document(HOSTS["ciRunners"], "codex-omarchy-nuc",
                                                          "~/.omp"))
            result = subprocess.run([sys.executable, "-c", rollout.RUNNERS_WRITER, "apply",
                                     "~/.omp/ci-runners.json", document], capture_output=True,
                                    text=True, check=False, env={"PATH": "/usr/bin:/bin", "HOME": home})
            self.assertEqual(result.stdout.strip(), "written")
            written = json.loads(Path(home, ".omp/ci-runners.json").read_text())
            self.assertEqual(written["runners"]["ci-gate"]["submit"][1],
                             home + "/.omp/scripts/ci-gate.py")

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
