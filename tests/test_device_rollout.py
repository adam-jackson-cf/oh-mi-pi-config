"""Device rollout classification without SSH, Infisical, omp or checkout side effects."""

import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.fast

MODULE = Path(__file__).resolve().parents[1] / "scripts/device_rollout.py"

spec = importlib.util.spec_from_file_location("device_rollout", MODULE)

assert spec is not None

assert spec.loader is not None

rollout = importlib.util.module_from_spec(spec)

spec.loader.exec_module(rollout)

HOSTS = json.loads(MODULE.with_name("device-rollout-hosts.json").read_text())

CURRENT = "a" * 40

OLD = "b" * 40


@dataclass
class Runner:
    """A fake command runner: answers rollout commands from its flags and records every argv."""

    stale: bool = False
    unreachable: bool = False
    dirty: bool = False
    behind: bool = False
    service_failed: bool = False
    malformed: bool = False
    pull_failed: bool = False
    coverage: str = "complete"
    find_output: str | None = None
    runners: str = "current"
    records: dict[str, list[object]] = field(default_factory=dict)
    commands: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        text = " ".join(argv)
        if argv[0] == "ssh" and self.unreachable:
            code, stdout = 255, ""
        else:
            code, stdout = (
                self.reads(text) or self.git(text) or self.service(text) or (0, "")
            )
        return subprocess.CompletedProcess(
            argv, code, stdout, "secret error not displayed"
        )

    def reads(self, text: str) -> tuple[int, str] | None:
        """Answers for the records lister, the runners writer and the find canary."""
        if "omp-records-list" in text:
            return 0, json.dumps(self.records)
        if "ci-runners.json" in text:
            return 0, self.runners + "\n"
        if "omp find" in text:
            return int(self.coverage == "unavailable"), (
                self.find_output
                if self.find_output is not None
                else json.dumps({"coverage": self.coverage, "hits": []})
            )
        return None

    def git(self, text: str) -> tuple[int, str] | None:
        """Answers for git revision, status and pull commands."""
        if "merge-base" in text:
            return int(self.behind), ""
        if "rev-list" in text:
            return 0, "3\n"
        if "rev-parse" in text:
            return 0, (OLD if "HEAD" in text and self.stale else CURRENT) + "\n"
        if "--porcelain" in text:
            return 0, " M tracked\n" if self.dirty else ""
        if "pull" in text and "--ff-only" in text:
            if not self.pull_failed:
                self.stale = False
            return int(self.pull_failed), ""
        return None

    def service(self, text: str) -> tuple[int, str] | None:
        """Answers for the deployment service and its state file."""
        if "systemctl" in text and "start" in text:
            return int(self.service_failed), ""
        if "systemctl" in text and "status" in text:
            return 3, "Active: failed (Result: exit-code)\nsecret log not displayed\n"
        if "cat" in text:
            return 0, "{}" if self.malformed else json.dumps({
                "revision": OLD if self.stale else CURRENT
            })
        return None


class Pipeline:
    def __init__(self, codes: tuple[int, int] = (0, 0), reason: str = "") -> None:
        self.codes = codes
        self.reason = reason
        self.calls: list[tuple[list[str], list[str]]] = []

    def __call__(
        self, producer: list[str], consumer: list[str]
    ) -> tuple[int, int, str]:
        self.calls.append((producer, consumer))
        return (*self.codes, self.reason)


def execute(
    runner: Runner,
    *,
    check: bool = True,
    hostname: str = "cdhvc2hw3l",
    pipeline: Pipeline | None = None,
    base: Path = Path("/base"),
) -> tuple[Any, int]:
    """Run a rollout against the fakes from a local home of /local; the rollout and its exit code."""
    machine = rollout.Machine(
        runner=runner,
        pipeline=pipeline or Pipeline(),
        home=Path("/local"),
        hostname=hostname,
        base=base,
    )
    deployment = rollout.Rollout(HOSTS, machine, check=check)
    return deployment, deployment.execute()


def row(deployment: Any, device: str, component: str) -> list[str]:
    found: list[str] = next(
        item for item in deployment.rows if item[:2] == [device, component]
    )
    return found


def checkouts(deployment: Any, skip: tuple[str, ...] = ()) -> list[list[str]]:
    """Checkout and deployment rows, without the ci-runners rows or skipped devices."""
    return [
        item
        for item in deployment.rows
        if item[1] != "ci-runners" and item[0] not in skip
    ]


class DeviceRolloutTests(unittest.TestCase):
    def test_current_and_read_only_check(self) -> None:
        runner = Runner()
        pipeline = Pipeline()
        deployment, code = execute(runner, pipeline=pipeline)
        assert pipeline.calls == []
        assert not any("pull" in cmd for cmd in runner.commands)
        assert code == 0
        assert {row[4] for row in checkouts(deployment, skip=("mac-cf2",))} == {
            "complete"
        }
        assert len(deployment.rows) == 8
        assert {row[5] for row in deployment.rows} == {"current"}
        assert not any(
            "pull" in " ".join(cmd) or "systemctl" in " ".join(cmd)
            for cmd in runner.commands
        )
        assert "device" in deployment.report()
        assert not any(row[:2] == ("mac-cf2", "omp") for row in deployment.rows)

    def test_local_label_resolves_normalized_hostname(self) -> None:
        deployment, code = execute(Runner(), hostname="CDHVC2HW3L.example.test")
        assert code == 0
        assert [row[0] for row in deployment.rows[:2]] == ["mac-cf3"] * 2

    def test_unknown_local_label_uses_short_hostname(self) -> None:
        deployment, code = execute(Runner(), hostname="OTHER-MAC.example.test")
        assert code == 0
        assert [row[0] for row in deployment.rows[:2]] == ["other-mac"] * 2

    def test_local_registry_host_is_not_contacted(self) -> None:
        runner = Runner()
        deployment, code = execute(runner, hostname="Adams-MacBook-Pro.local")
        assert code == 0
        assert [row[0] for row in deployment.rows[:2]] == ["mac-cf2"] * 2
        assert len(deployment.rows) == 6
        assert [row[:2] for row in deployment.rows[2:]] == [
            ["mac-cf2", "ci-runners"],
            ["codex-omarchy-nuc", "agents"],
            ["codex-omarchy-nuc", "omp"],
            ["codex-omarchy-nuc", "ci-runners"],
        ]
        assert all(cmd[5] != "mac-cf2" for cmd in runner.commands if cmd[0] == "ssh")

    def test_stale_is_nonzero(self) -> None:
        deployment, code = execute(Runner(stale=True))
        assert code == 1
        assert {row[5] for row in checkouts(deployment)} == {"stale"}

    def test_unreachable_is_nonzero_and_does_not_crash(self) -> None:
        deployment, code = execute(Runner(unreachable=True))
        assert code == 1
        remote = deployment.rows[3:]
        assert [row[5] for row in remote] == ["unreachable"] * 5
        assert [row[4] for row in checkouts(deployment, ("mac-cf3",))] == [
            "unreachable"
        ] * 3
        assert row(deployment, "mac-cf3", "ci-runners")[5] == "current"
        assert "secret" not in deployment.report()

    def test_dirty_blocks_pull_in_both_modes(self) -> None:
        for check in (False, True):
            runner = Runner(dirty=True)
            deployment, code = execute(runner, check=check)
            assert code == 1
            assert row(deployment, "mac-cf2", "agents")[5] == "dirty-blocked"
            assert not any("--ff-only" in " ".join(cmd) for cmd in runner.commands)
            assert any(
                "--untracked-files=no" in " ".join(cmd) for cmd in runner.commands
            )

    def test_nuc_config_behind_is_nonzero_even_when_deployed_matches(self) -> None:
        deployment, code = execute(Runner(behind=True))
        assert code == 1
        assert row(deployment, "codex-omarchy-nuc", "omp")[5] == "nuc-config-behind"
        assert (
            "nuc-config behind main by 3 commits: open an agent/nuc-sync-<date> PR"
            in deployment.report()
        )

    def test_apply_pulls_only_remote_and_starts_service(self) -> None:
        runner = Runner(stale=True)
        deployment, code = execute(runner, check=False)
        assert code == 1  # Local revisions are never pulled.
        pulls = [cmd for cmd in runner.commands if "--ff-only" in " ".join(cmd)]
        assert len(pulls) == 2
        assert all(cmd[0] == "ssh" for cmd in pulls)
        assert [row[5] for row in checkouts(deployment, ("mac-cf3",))] == [
            "current"
        ] * 3
        assert any(
            "systemctl --user start omp-config-pull.service" in " ".join(cmd)
            for cmd in runner.commands
        )
        for cmd in runner.commands:
            if cmd[0] == "ssh":
                assert cmd[1:5] == ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
                if cmd[5] == "codex-omarchy-nuc":
                    assert cmd[6].startswith("bash -lc ")
                    assert "git -C" not in (
                        cmd[6].replace(".agents", "") if ".omp" in cmd[6] else ""
                    )

    def test_service_failure_reports_safe_status(self) -> None:
        runner = Runner(service_failed=True)
        deployment, code = execute(runner, check=False)
        assert code == 1
        assert row(deployment, "codex-omarchy-nuc", "omp")[5] == "failed"
        assert "Active: failed" in deployment.report()
        assert "secret" not in deployment.report()

    def test_malformed_deployment_is_failed(self) -> None:
        deployment, code = execute(Runner(malformed=True))
        assert code == 1
        assert row(deployment, "codex-omarchy-nuc", "omp")[5] == "failed"

    def test_failed_pull_is_failed(self) -> None:
        deployment, code = execute(Runner(pull_failed=True), check=False)
        assert code == 1
        assert row(deployment, "mac-cf2", "agents")[5] == "failed"

    def test_cli_exit_and_table_use_injected_runner(self) -> None:
        for runner, expected in ((Runner(), 0), (Runner(unreachable=True), 1)):
            pipeline = Pipeline()
            with redirect_stdout(io.StringIO()) as output:
                machine = rollout.Machine(
                    runner=runner, pipeline=pipeline, base=Path("/base")
                )
                assert rollout.main(["--check"], machine) == expected
            assert "deployed" in output.getvalue()
            assert "find" in output.getvalue().splitlines()[0]
            assert pipeline.calls == []

    def test_apply_streams_judge_key_to_every_device(self) -> None:
        pipeline = Pipeline()
        deployment, code = execute(Runner(), check=False, pipeline=pipeline)
        assert code == 0
        assert len(pipeline.calls) == 2
        key, source = HOSTS["judgeKey"], HOSTS["infisical"]
        for producer, consumer in pipeline.calls:
            assert producer[:7] == [
                "infisical",
                "run",
                "--silent",
                "--projectId",
                source["project"],
                "--env",
                source["environment"],
            ]
            assert producer[-2:] == [key["secret"], key["provider"]]
            assert (
                consumer == ["omp", "auth", "import", "-"]
                or consumer[0] == "ssh"
                and "omp auth import -" in consumer[-1]
            )
        assert [call[1][5] for call in pipeline.calls[1:]] == ["codex-omarchy-nuc"]
        assert pipeline.calls[1][1][6].startswith("bash -lc ")

    def test_devices_without_omp_skip_key_and_find(self) -> None:
        runner, pipeline = Runner(), Pipeline((3, 1))
        deployment, _ = execute(runner, check=False, pipeline=pipeline)
        assert "mac-cf2" not in [
            call[1][5] for call in pipeline.calls if call[1][0] == "ssh"
        ]
        assert not any(
            "omp find" in " ".join(cmd) and "mac-cf2" in cmd for cmd in runner.commands
        )
        assert row(deployment, "mac-cf2", "agents")[4:] == ["-", "current"]
        assert "mac-cf2: judge key" not in deployment.report()

    def test_key_failure_keeps_rows_non_current(self) -> None:
        deployment, code = execute(Runner(), check=False, pipeline=Pipeline((3, 1)))
        assert code == 1
        assert {row[4] for row in checkouts(deployment, skip=("mac-cf2",))} == {
            "key-failed"
        }
        assert {row[5] for row in checkouts(deployment, skip=("mac-cf2",))} == {
            "find-blocked"
        }
        assert (
            "judge key provisioning failed (infisical exited 3)" in deployment.report()
        )

    def test_key_failure_names_the_import_error_without_credential_material(
        self,
    ) -> None:
        reason = rollout.failure_line(
            b"warming up\nerror: credential sk-or-v1-0123456789abcdef0123 rejected\n"
        )
        assert reason == "error: credential <redacted> rejected"
        deployment, code = execute(
            Runner(), check=False, pipeline=Pipeline((0, 1), reason)
        )
        assert code == 1
        assert (
            "judge key provisioning failed (omp auth import exited 1: "
            "error: credential <redacted> rejected)" in deployment.report()
        )
        assert "0123456789abcdef" not in deployment.report()

    def test_non_complete_find_keeps_rows_non_current(self) -> None:
        for coverage, output in (
            ("partial", None),
            ("unavailable", None),
            ("failed", "not json"),
            ("failed", json.dumps({"coverage": "odd"})),
        ):
            deployment, code = execute(Runner(coverage=coverage, find_output=output))
            assert code == 1
            assert {row[4] for row in checkouts(deployment, skip=("mac-cf2",))} == {
                coverage
            }
            assert {row[5] for row in checkouts(deployment, skip=("mac-cf2",))} == {
                "find-blocked"
            }

    def test_find_canary_runs_on_configured_path(self) -> None:
        runner = Runner()
        execute(runner)
        finds = [
            " ".join(cmd) for cmd in runner.commands if "omp find" in " ".join(cmd)
        ]
        assert len(finds) == 2
        assert "/local/.agents/scripts --json -q" in finds[0]
        assert all("--json -q" in text for text in finds[1:])


class RunnerConfigTests(unittest.TestCase):
    """ci-runners.json documents, their writer, and the judge key builder and pipeline."""

    def test_ci_runners_row_per_device_and_check_is_read_only(self) -> None:
        for state in ("missing", "stale"):
            runner = Runner(runners=state)
            deployment, code = execute(runner)
            assert code == 1
            rows = [item for item in deployment.rows if item[1] == "ci-runners"]
            assert [item[0] for item in rows] == [
                "mac-cf3",
                "mac-cf2",
                "codex-omarchy-nuc",
            ]
            assert {item[5] for item in rows} == {state}
            assert (
                f"mac-cf2: ci-runners.json {state}; run device_rollout.py without --check"
                in deployment.report()
            )
            writes = [
                cmd for cmd in runner.commands if "ci-runners.json" in " ".join(cmd)
            ]
            assert len(writes) == 3
            assert all(
                " check " in " ".join(cmd) and " apply " not in " ".join(cmd)
                for cmd in writes
            )
        runner = Runner(runners="written")
        deployment, code = execute(runner, check=False)
        assert code == 0
        assert row(deployment, "codex-omarchy-nuc", "ci-runners")[5] == "current"
        local, mac, nuc = [
            cmd for cmd in runner.commands if "ci-runners.json" in " ".join(cmd)
        ]
        assert local[0] == sys.executable
        assert local[4] == "/base/ci-runners.json"
        assert mac[:6] == [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "mac-cf2",
        ]
        assert "python3 -c" in mac[6]
        assert "~/.omp/ci-runners.json" in mac[6]
        assert nuc[6].startswith("bash -lc ")

    def test_ci_runners_failure_is_nonzero(self) -> None:
        deployment, code = execute(Runner(runners="Traceback"))
        assert code == 1
        assert row(deployment, "mac-cf3", "ci-runners")[5] == "failed"

    def test_runner_document_local_on_ci_gate_host_and_ssh_elsewhere(self) -> None:
        definition = HOSTS["ciRunners"]
        host = definition["roles"]["ci-gate"]["host"]
        infisical = {"project": "test-project", "environment": "test-env"}
        settings = [
            "CI_GATE_INFISICAL_PROJECT=test-project",
            "CI_GATE_INFISICAL_ENV=test-env",
        ]
        on_host = rollout.runner_document(definition, host, "~/.omp", infisical)[
            "runners"
        ]
        assert on_host["ci-gate"]["wait"] == [
            "env",
            *settings,
            "python3",
            "~/.omp/scripts/ci_gate.py",
            "wait",
            "{repo}",
            "{sha}",
            "--timeout",
            "{timeout}",
        ]
        elsewhere = rollout.runner_document(definition, "mac-cf3", "/base", infisical)[
            "runners"
        ]
        prefix = (
            'bash -lc "' + " ".join(settings) + " python3 ~/.omp/scripts/ci_gate.py "
        )
        assert elsewhere["ci-gate"]["submit"] == [
            "ssh",
            "-o",
            "BatchMode=yes",
            host,
            prefix + 'submit {repo} {sha}"',
        ]
        assert (
            elsewhere["ci-gate"]["logs"][-1]
            == prefix + 'logs {repo} {sha} --tail {tail}"'
        )
        for runners in (on_host, elsewhere):
            for role, spec in definition["roles"].items():
                assert runners[role]["timeout"] == spec["timeout"]
            actions = runners["github-actions"]
            assert [actions[name] for name in ("submit", "wait", "status")] == [
                None
            ] * 3
            assert "--log-failed" in actions["logs"][2]
            assert actions["logs"][-3:] == ["{repo}", "{sha}", "{tail}"]
        for bad, named in (
            ({**definition, "remoteBase": "/abs; rm"}, "remoteBase"),
            (
                {**definition, "roles": {"x": {"kind": "ci-gate", "host": "a b"}}},
                "ssh alias",
            ),
            (
                {**definition, "roles": {"x": {"kind": "other"}}},
                "unknown ciRunners kind",
            ),
            (
                {**definition, "roles": {"x": {"kind": "github-actions"}}},
                "positive timeout",
            ),
            (
                {
                    **definition,
                    "roles": {"x": {"kind": "github-actions", "timeout": 0}},
                },
                "positive timeout",
            ),
        ):
            with self.subTest(named=named), pytest.raises(ValueError, match=named):
                rollout.runner_document(bad, "mac-cf3", "/base", infisical)
        for setting in (
            None,
            {},
            {"project": "p"},
            {"project": "p; rm", "environment": "e"},
            {"project": "p", "environment": ""},
        ):
            with (
                self.subTest(infisical=setting),
                pytest.raises(ValueError, match="infisical"),
            ):
                rollout.runner_document(definition, "mac-cf3", "/base", setting)

    def test_registry_holds_infisical_project_once_for_key_and_runners(self) -> None:
        text = MODULE.with_name("device-rollout-hosts.json").read_text()
        assert text.count(HOSTS["infisical"]["project"]) == 1
        assert "infisicalProject" not in HOSTS["judgeKey"]
        runners = rollout.runner_document(
            HOSTS["ciRunners"], "mac-cf3", "/base", HOSTS["infisical"]
        )
        assert (
            "CI_GATE_INFISICAL_PROJECT=" + HOSTS["infisical"]["project"]
            in runners["runners"]["ci-gate"]["submit"][-1]
        )
        assert HOSTS["infisical"]["project"] not in MODULE.read_text()
        assert (
            HOSTS["infisical"]["project"]
            not in MODULE.with_name("ci_gate.py").read_text()
        )

    def test_missing_infisical_setting_fails_key_and_runner_rows(self) -> None:
        hosts = {key: value for key, value in HOSTS.items() if key != "infisical"}
        pipeline = Pipeline()
        machine = rollout.Machine(
            runner=Runner(runners="written"),
            pipeline=pipeline,
            home=Path("/local"),
            hostname="cdhvc2hw3l",
            base=Path("/base"),
        )
        deployment = rollout.Rollout(hosts, machine, check=False)
        assert deployment.execute() == 1
        assert pipeline.calls == []
        assert "judge key provisioning failed" in deployment.report()
        assert {item[5] for item in deployment.rows if item[1] == "ci-runners"} == {
            "failed"
        }


class RecordRolloutCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="records-rollout-test-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.base = self.scratch / "source"
        self.write(
            self.base,
            "example-owner/example-repo/gate.json",
            '{"schemaVersion": 2}\n',
            1_000,
        )
        self.write(
            self.base, "example-owner/example-repo/profile.md", "---\n{}\n---\n", 1_000
        )
        self.source = rollout.record_files(self.base / "repos")

    @staticmethod
    def write(base: Path, relative: str, text: str, mtime_s: int) -> Path:
        path = base / "repos" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        os.utime(path, (mtime_s, mtime_s))
        return path

    def execute(
        self, runner: Runner, *, check: bool = True, pipeline: Pipeline | None = None
    ) -> tuple[Any, int]:
        return execute(runner, check=check, pipeline=pipeline, base=self.base)

    def target(self, **changes: list[object]) -> dict[str, list[object]]:
        listing: dict[str, list[object]] = {
            relative: list(value) for relative, value in self.source.items()
        }
        listing.update(changes)
        return listing


class RecordRolloutTests(RecordRolloutCase):
    """Per-repo records go one way from the rollout source to remote omp devices."""

    def test_records_go_only_to_devices_that_run_omp(self) -> None:
        deployment, code = self.execute(Runner(records=self.target()))
        assert code == 0
        assert [item[:2] for item in deployment.rows if item[1] == "records"] == [
            ["codex-omarchy-nuc", "records"]
        ]
        assert row(deployment, "codex-omarchy-nuc", "records")[5] == "current"

    def test_check_reports_missing_records_without_copying(self) -> None:
        pipeline = Pipeline()
        deployment, code = self.execute(Runner(records={}), pipeline=pipeline)
        assert code == 1
        assert row(deployment, "codex-omarchy-nuc", "records")[5] == "stale"
        assert pipeline.calls == []
        assert "codex-omarchy-nuc: 2 record file(s) to copy" in deployment.report()

    def test_apply_streams_changed_files_to_the_target_base(self) -> None:
        pipeline = Pipeline()
        older = [1, "0" * 64]
        deployment, code = self.execute(
            Runner(
                records=self.target(**{"example-owner/example-repo/gate.json": older})
            ),
            check=False,
            pipeline=pipeline,
        )
        assert code == 0
        assert row(deployment, "codex-omarchy-nuc", "records")[5] == "current"
        copies = [
            call
            for call in pipeline.calls
            if call[0][1:3] == ["-c", rollout.RECORDS_BUNDLER]
        ]
        assert len(copies) == 1
        producer, consumer = copies[0]
        assert producer[3:] == [
            str(self.base / "repos"),
            "example-owner/example-repo/gate.json",
        ]
        assert consumer[:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
        assert consumer[5] == "codex-omarchy-nuc"
        assert "omp-records-write" in consumer[6]
        assert shlex.split(shlex.split(consumer[6])[-1])[-2:] == [
            "~/.omp",
            json.dumps({"example-owner/example-repo/gate.json": older}),
        ], "the writer receives the base and the target state the plan saw"

    def test_newer_target_record_is_refused_and_reported(self) -> None:
        pipeline = Pipeline()
        newer = [self.source["example-owner/example-repo/gate.json"][0] + 1, "f" * 64]
        runner = Runner(
            records=self.target(**{
                "example-owner/example-repo/gate.json": newer,
                "example-owner/example-repo/profile.md": [1, "0" * 64],
            })
        )
        deployment, code = self.execute(runner, check=False, pipeline=pipeline)
        assert code == 1
        assert row(deployment, "codex-omarchy-nuc", "records")[5] == "record-newer"
        assert not any(
            call[0][1:3] == ["-c", rollout.RECORDS_BUNDLER] for call in pipeline.calls
        ), "no file of a newer target record may be overwritten"
        assert (
            "codex-omarchy-nuc: record example-owner/example-repo is newer on the target; not overwritten"
            in deployment.report()
        )

    def test_unreachable_target_records_row(self) -> None:
        deployment, code = self.execute(Runner(unreachable=True))
        assert code == 1
        assert row(deployment, "codex-omarchy-nuc", "records")[5] == "unreachable"

    def test_no_source_records_adds_no_row(self) -> None:
        self.base = self.scratch / "empty"
        deployment, code = self.execute(Runner())
        assert code == 0
        assert not any(item[1] == "records" for item in deployment.rows)

    def test_plan_copies_missing_and_older_and_leaves_target_only_files(self) -> None:
        gate, profile = "o/n/gate.json", "o/n/profile.md"
        source = {gate: (10, "a"), profile: (10, "b"), "o/other/gate.json": (10, "c")}
        target = {
            gate: (10, "a"),
            profile: (5, "old"),
            "o/n/extra.md": (99, "x"),
            "o/other/gate.json": (11, "edited"),
        }
        assert rollout.record_plan(source, target) == ([profile], ["o/other"])
        assert rollout.record_plan(source, {}) == (sorted(source), [])

    def test_apply_reports_files_the_writer_refused(self) -> None:
        older = [1, "0" * 64]
        runner = Runner(
            records=self.target(**{"example-owner/example-repo/gate.json": older})
        )
        deployment, code = self.execute(
            runner, check=False, pipeline=Pipeline(codes=(0, 5))
        )
        assert code == 1
        assert row(deployment, "codex-omarchy-nuc", "records")[5] == "record-newer"
        assert (
            "codex-omarchy-nuc: record file example-owner/example-repo/gate.json changed on the target "
            "after inventory; not overwritten" in deployment.report()
        )


if __name__ == "__main__":
    unittest.main()
