"""Device rollout classification without SSH, Infisical, omp or checkout side effects."""
import importlib.util
import io
import json
import os
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
                 coverage="complete", husks="", find_output=None, runners="current", records=None):
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
        self.records = {} if records is None else records
        self.commands = []

    def __call__(self, argv):
        self.commands.append(argv)
        text = " ".join(argv)
        remote = argv[0] == "ssh"
        code, stdout = 0, ""
        if remote and self.unreachable:
            code = 255
        elif "omp-records-list" in text:
            stdout = json.dumps(self.records)
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
        self.assertEqual({row[4] for row in checkouts(deployment, skip=("mac-cf2",))}, {"complete"})
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
                self.assertEqual(rollout.main(["--check"], runner=runner, pipeline=pipeline,
                                              base="/base"), expected)
            self.assertIn("deployed", output.getvalue())
            self.assertIn("find", output.getvalue().splitlines()[0])
            self.assertEqual(pipeline.calls, [])

    def test_apply_streams_judge_key_to_every_device(self):
        pipeline = Pipeline()
        deployment, code = self.execute(Runner(), check=False, pipeline=pipeline)
        self.assertEqual(code, 0)
        self.assertEqual(len(pipeline.calls), 2)
        key, source = HOSTS["judgeKey"], HOSTS["infisical"]
        for producer, consumer in pipeline.calls:
            self.assertEqual(producer[:7], ["infisical", "run", "--silent", "--projectId",
                                            source["project"], "--env", source["environment"]])
            self.assertEqual(producer[-2:], [key["secret"], key["provider"]])
            self.assertTrue(consumer == ["omp", "auth", "import", "-"]
                            or (consumer[0] == "ssh" and "omp auth import -" in consumer[-1]))
        self.assertEqual([call[1][5] for call in pipeline.calls[1:]], ["codex-omarchy-nuc"])
        self.assertTrue(pipeline.calls[1][1][6].startswith("bash -lc "))

    def test_devices_without_omp_skip_key_and_find(self):
        runner, pipeline = Runner(), Pipeline((3, 1))
        deployment, _ = self.execute(runner, check=False, pipeline=pipeline)
        self.assertNotIn("mac-cf2", [call[1][5] for call in pipeline.calls if call[1][0] == "ssh"])
        self.assertFalse(any("omp find" in " ".join(cmd) and "mac-cf2" in cmd for cmd in runner.commands))
        self.assertEqual(row(deployment, "mac-cf2", "agents")[4:], ["-", "current"])
        self.assertNotIn("mac-cf2: judge key", deployment.report())

    def test_key_failure_keeps_rows_non_current(self):
        deployment, code = self.execute(Runner(), check=False, pipeline=Pipeline((3, 1)))
        self.assertEqual(code, 1)
        self.assertEqual({row[4] for row in checkouts(deployment, skip=("mac-cf2",))}, {"key-failed"})
        self.assertEqual({row[5] for row in checkouts(deployment, skip=("mac-cf2",))}, {"find-blocked"})
        self.assertIn("judge key provisioning failed", deployment.report())

    def test_non_complete_find_keeps_rows_non_current(self):
        for coverage, output in (("partial", None), ("unavailable", None), ("failed", "not json"),
                                 ("failed", json.dumps({"coverage": "odd"}))):
            deployment, code = self.execute(Runner(coverage=coverage, find_output=output))
            self.assertEqual(code, 1)
            self.assertEqual({row[4] for row in checkouts(deployment, skip=("mac-cf2",))}, {coverage})
            self.assertEqual({row[5] for row in checkouts(deployment, skip=("mac-cf2",))}, {"find-blocked"})

    def test_find_canary_runs_on_configured_path(self):
        runner = Runner()
        self.execute(runner)
        finds = [" ".join(cmd) for cmd in runner.commands if "omp find" in " ".join(cmd)]
        self.assertEqual(len(finds), 2)
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
        infisical = {"project": "test-project", "environment": "test-env"}
        settings = ["CI_GATE_INFISICAL_PROJECT=test-project", "CI_GATE_INFISICAL_ENV=test-env"]
        on_host = rollout.runner_document(definition, host, "~/.omp", infisical)["runners"]
        self.assertEqual(on_host["ci-gate"]["wait"],
                         ["env", *settings, "python3", "~/.omp/scripts/ci-gate.py", "wait", "{repo}",
                          "{sha}", "--timeout", "{timeout}"])
        elsewhere = rollout.runner_document(definition, "mac-cf3", "/base", infisical)["runners"]
        prefix = 'bash -lc "' + " ".join(settings) + " python3 ~/.omp/scripts/ci-gate.py "
        self.assertEqual(elsewhere["ci-gate"]["submit"],
                         ["ssh", "-o", "BatchMode=yes", host, prefix + 'submit {repo} {sha}"'])
        self.assertEqual(elsewhere["ci-gate"]["logs"][-1], prefix + 'logs {repo} {sha} --tail {tail}"')
        for runners in (on_host, elsewhere):
            for role, spec in definition["roles"].items():
                self.assertEqual(runners[role]["timeout"], spec["timeout"])
            actions = runners["github-actions"]
            self.assertEqual([actions[name] for name in ("submit", "wait", "status")], [None] * 3)
            self.assertIn("--log-failed", actions["logs"][2])
            self.assertEqual(actions["logs"][-3:], ["{repo}", "{sha}", "{tail}"])
        for bad in ({**definition, "remoteBase": "/abs; rm"},
                    {**definition, "roles": {"x": {"kind": "ci-gate", "host": "a b"}}},
                    {**definition, "roles": {"x": {"kind": "other"}}},
                    {**definition, "roles": {"x": {"kind": "github-actions"}}},
                    {**definition, "roles": {"x": {"kind": "github-actions", "timeout": 0}}}):
            with self.assertRaises(ValueError):
                rollout.runner_document(bad, "mac-cf3", "/base", infisical)
        for bad in (None, {}, {"project": "p"}, {"project": "p; rm", "environment": "e"},
                    {"project": "p", "environment": ""}):
            with self.subTest(infisical=bad), self.assertRaisesRegex(ValueError, "infisical"):
                rollout.runner_document(definition, "mac-cf3", "/base", bad)

    def test_registry_holds_infisical_project_once_for_key_and_runners(self):
        text = MODULE.with_name("device-rollout-hosts.json").read_text()
        self.assertEqual(text.count(HOSTS["infisical"]["project"]), 1)
        self.assertNotIn("infisicalProject", HOSTS["judgeKey"])
        runners = rollout.runner_document(HOSTS["ciRunners"], "mac-cf3", "/base", HOSTS["infisical"])
        self.assertIn("CI_GATE_INFISICAL_PROJECT=" + HOSTS["infisical"]["project"],
                      runners["runners"]["ci-gate"]["submit"][-1])
        self.assertNotIn(HOSTS["infisical"]["project"], MODULE.read_text())
        self.assertNotIn(HOSTS["infisical"]["project"],
                         MODULE.with_name("ci-gate.py").read_text())

    def test_missing_infisical_setting_fails_key_and_runner_rows(self):
        hosts = {key: value for key, value in HOSTS.items() if key != "infisical"}
        pipeline = Pipeline()
        deployment = rollout.Rollout(hosts, check=False, runner=Runner(runners="written"),
                                     home="/local", hostname="cdhvc2hw3l", pipeline=pipeline,
                                     base="/base")
        self.assertEqual(deployment.execute(), 1)
        self.assertEqual(pipeline.calls, [])
        self.assertIn("judge key provisioning failed", deployment.report())
        self.assertEqual({item[5] for item in deployment.rows if item[1] == "ci-runners"}, {"failed"})

    def test_runners_writer_is_idempotent_and_feeds_ci_runner(self):
        with tempfile.TemporaryDirectory() as base:
            deployment = rollout.Rollout({"ciRunners": HOSTS["ciRunners"],
                                          "infisical": HOSTS["infisical"]}, check=True,
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
                                                          "~/.omp", HOSTS["infisical"]))
            result = subprocess.run([sys.executable, "-c", rollout.RUNNERS_WRITER, "apply",
                                     "~/.omp/ci-runners.json", document], capture_output=True,
                                    text=True, check=False, env={"PATH": "/usr/bin:/bin", "HOME": home})
            self.assertEqual(result.stdout.strip(), "written")
            written = json.loads(Path(home, ".omp/ci-runners.json").read_text())
            self.assertEqual(written["runners"]["ci-gate"]["submit"][4],
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


class RecordRolloutTests(unittest.TestCase):
    """Per-repo records go one way from the rollout source to remote omp devices."""
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="records-rollout-test-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.base = self.scratch / "source"
        self.write(self.base, "example-owner/example-repo/gate.json", '{"schemaVersion": 2}\n', 1_000)
        self.write(self.base, "example-owner/example-repo/profile.md", "---\n{}\n---\n", 1_000)
        self.source = rollout.record_files(self.base / "repos")

    @staticmethod
    def write(base, relative, text, mtime_s):
        path = base / "repos" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        os.utime(path, (mtime_s, mtime_s))
        return path

    def execute(self, runner, check=True, pipeline=None):
        deployment = rollout.Rollout(HOSTS, check=check, runner=runner, home="/local",
                                     hostname="cdhvc2hw3l", pipeline=pipeline or Pipeline(),
                                     base=self.base)
        return deployment, deployment.execute()

    def target(self, **changes):
        listing = {relative: list(value) for relative, value in self.source.items()}
        listing.update(changes)
        return listing

    def test_records_go_only_to_devices_that_run_omp(self):
        deployment, code = self.execute(Runner(records=self.target()))
        self.assertEqual(code, 0)
        self.assertEqual([item[:2] for item in deployment.rows if item[1] == "records"],
                         [["codex-omarchy-nuc", "records"]])
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "records")[5], "current")

    def test_check_reports_missing_records_without_copying(self):
        pipeline = Pipeline()
        deployment, code = self.execute(Runner(records={}), pipeline=pipeline)
        self.assertEqual(code, 1)
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "records")[5], "stale")
        self.assertEqual(pipeline.calls, [])
        self.assertIn("codex-omarchy-nuc: 2 record file(s) to copy", deployment.report())

    def test_apply_streams_changed_files_to_the_target_base(self):
        pipeline = Pipeline()
        older = [1, "0" * 64]
        deployment, code = self.execute(Runner(records=self.target(**{"example-owner/example-repo/gate.json": older})),
                                        check=False, pipeline=pipeline)
        self.assertEqual(code, 0)
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "records")[5], "current")
        copies = [call for call in pipeline.calls if call[0][1:3] == ["-c", rollout.RECORDS_BUNDLER]]
        self.assertEqual(len(copies), 1)
        producer, consumer = copies[0]
        self.assertEqual(producer[3:], [str(self.base / "repos"), "example-owner/example-repo/gate.json"])
        self.assertEqual(consumer[:5], ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"])
        self.assertEqual(consumer[5], "codex-omarchy-nuc")
        self.assertIn("omp-records-write", consumer[6])
        self.assertTrue(consumer[6].rstrip("'\"").endswith("~/.omp"), consumer[6])

    def test_newer_target_record_is_refused_and_reported(self):
        pipeline = Pipeline()
        newer = [self.source["example-owner/example-repo/gate.json"][0] + 1, "f" * 64]
        runner = Runner(records=self.target(**{"example-owner/example-repo/gate.json": newer,
                                                "example-owner/example-repo/profile.md": [1, "0" * 64]}))
        deployment, code = self.execute(runner, check=False, pipeline=pipeline)
        self.assertEqual(code, 1)
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "records")[5], "record-newer")
        self.assertFalse(any(call[0][1:3] == ["-c", rollout.RECORDS_BUNDLER] for call in pipeline.calls),
                         "no file of a newer target record may be overwritten")
        self.assertIn("codex-omarchy-nuc: record example-owner/example-repo is newer on the target; not overwritten",
                      deployment.report())

    def test_unreachable_target_records_row(self):
        deployment, code = self.execute(Runner(unreachable=True))
        self.assertEqual(code, 1)
        self.assertEqual(row(deployment, "codex-omarchy-nuc", "records")[5], "unreachable")

    def test_no_source_records_adds_no_row(self):
        self.base = self.scratch / "empty"
        deployment, code = self.execute(Runner())
        self.assertEqual(code, 0)
        self.assertFalse(any(item[1] == "records" for item in deployment.rows))

    def test_plan_copies_missing_and_older_and_leaves_target_only_files(self):
        gate, profile = "o/n/gate.json", "o/n/profile.md"
        source = {gate: (10, "a"), profile: (10, "b"), "o/other/gate.json": (10, "c")}
        target = {gate: (10, "a"), profile: (5, "old"), "o/n/extra.md": (99, "x"),
                  "o/other/gate.json": (11, "edited")}
        self.assertEqual(rollout.record_plan(source, target), ([profile], ["o/other"]))
        self.assertEqual(rollout.record_plan(source, {}), (sorted(source), []))

    def test_bundler_writer_and_lister_round_trip_on_a_real_target(self):
        home = self.scratch / "home"
        (home / ".omp").mkdir(parents=True)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
        files = sorted(self.source)
        producer = [sys.executable, "-c", rollout.RECORDS_BUNDLER, str(self.base / "repos"), *files]
        bundle = subprocess.run(producer, capture_output=True, check=True)
        written = subprocess.run([sys.executable, "-c", rollout.RECORDS_WRITER, "~/.omp"], input=bundle.stdout,
                                 capture_output=True, env=env, check=False)
        self.assertEqual(written.returncode, 0, written.stderr)
        for relative in files:
            target = home / ".omp/repos" / relative
            self.assertEqual(target.read_bytes(), (self.base / "repos" / relative).read_bytes())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        listed = subprocess.run([sys.executable, "-c", rollout.RECORDS_LISTER, "~/.omp"],
                                capture_output=True, text=True, env=env, check=True)
        listing = {key: tuple(value) for key, value in json.loads(listed.stdout).items()}
        self.assertEqual(listing, self.source, "the copy keeps source bytes and mtimes")
        self.assertEqual(rollout.record_plan(self.source, listing), ([], []))
        self.write(home / ".omp", "example-owner/example-repo/gate.json", "{}\n", 2_000)
        listing = rollout.record_files(home / ".omp/repos")
        self.assertEqual(rollout.record_plan(self.source, listing), ([], ["example-owner/example-repo"]))

    def test_writer_refuses_paths_that_leave_the_records_root(self):
        home = self.scratch / "home"
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
        for relative in ("../escape", "/abs", "o/./n", ""):
            with self.subTest(relative=relative):
                document = json.dumps({relative: [1, "eA=="]}).encode()
                result = subprocess.run([sys.executable, "-c", rollout.RECORDS_WRITER, "~/.omp"],
                                        input=document, capture_output=True, env=env, check=False)
                self.assertEqual(result.returncode, 4)
        self.assertFalse((self.scratch / "escape").exists())


if __name__ == "__main__":
    unittest.main()
