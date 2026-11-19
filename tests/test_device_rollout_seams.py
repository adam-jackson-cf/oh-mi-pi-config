"""Device rollout classification without SSH, Infisical, omp or checkout side effects: the T2 seams.

These tests start processes or drive real Git repositories, so they run in T2.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest
from test_device_rollout import HOSTS, MODULE, RecordRolloutCase, rollout

pytestmark = pytest.mark.seam


PROCESS_TIMEOUT = 60


class RunnerConfigTests(unittest.TestCase):
    """ci-runners.json documents, their writer, and the judge key builder and pipeline."""

    def test_runners_writer_is_idempotent_and_feeds_ci_runner(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            machine = rollout.Machine(
                runner=rollout.run_command,
                home=Path(base),
                hostname="dev",
                base=Path(base),
            )
            deployment = rollout.Rollout(
                {"ciRunners": HOSTS["ciRunners"], "infisical": HOSTS["infisical"]},
                machine,
                check=True,
            )
            target = Path(base) / "ci-runners.json"
            deployment.runners("dev", 0)
            assert deployment.rows[-1] == [
                "dev",
                "ci-runners",
                "-",
                "-",
                "-",
                "missing",
            ]
            assert not target.exists()
            deployment.check = False
            deployment.runners("dev", 0)
            written = target.read_text()
            mtime = target.stat().st_mtime_ns
            deployment.runners("dev", 0)
            deployment.check = True
            deployment.runners("dev", 0)
            assert [item[5] for item in deployment.rows[1:]] == ["current"] * 3
            assert target.read_text() == written
            assert target.stat().st_mtime_ns == mtime
            document = json.loads(written)
            assert document["schemaVersion"] == 1
            assert sorted(document["runners"]) == ["ci-gate", "github-actions"]
            target.write_text("{}")
            deployment.runners("dev", 0)
            assert deployment.rows[-1][5] == "stale"
            assert target.read_text() == "{}"
            deployment.check = False
            deployment.runners("dev", 0)
            assert target.read_text() == written
            cli = MODULE.with_name("ci_runner.py")
            result = subprocess.run(
                [sys.executable, str(cli), "submit", "github-actions", "o/r", "a" * 40],
                capture_output=True,
                text=True,
                check=False,
                env={"PATH": "/usr/bin:/bin", "OMP_CI_RUNNERS": str(target)},
                timeout=PROCESS_TIMEOUT,
            )
            assert result.returncode == 0
            assert "starts on push" in result.stdout

    def test_writer_expands_home_paths_on_target(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            Path(home, ".omp").mkdir()
            document = json.dumps(
                rollout.runner_document(
                    HOSTS["ciRunners"],
                    "codex-omarchy-nuc",
                    "~/.omp",
                    HOSTS["infisical"],
                )
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    rollout.RUNNERS_WRITER,
                    "apply",
                    "~/.omp/ci-runners.json",
                    document,
                ],
                capture_output=True,
                text=True,
                check=False,
                env={"PATH": "/usr/bin:/bin", "HOME": home},
                timeout=PROCESS_TIMEOUT,
            )
            assert result.stdout.strip() == "written"
            written = json.loads(Path(home, ".omp/ci-runners.json").read_text())
            assert (
                written["runners"]["ci-gate"]["submit"][4]
                == home + "/.omp/scripts/ci_gate.py"
            )

    def test_credential_builder_emits_auth_import_document(self) -> None:
        env = {"PATH": "/usr/bin:/bin", "FAKE_JUDGE_KEY": "fake-value"}
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                rollout.CREDENTIAL_BUILDER,
                "FAKE_JUDGE_KEY",
                "openrouter",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=PROCESS_TIMEOUT,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {
            "version": 1,
            "credentials": [
                {
                    "provider": "openrouter",
                    "credential": {"type": "api_key", "key": "fake-value"},
                }
            ],
        }
        missing = subprocess.run(
            [
                sys.executable,
                "-c",
                rollout.CREDENTIAL_BUILDER,
                "FAKE_JUDGE_KEY",
                "openrouter",
            ],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin"},
            check=False,
            timeout=PROCESS_TIMEOUT,
        )
        assert (missing.returncode, missing.stdout) == (3, "")

    def test_pipeline_streams_stdout_to_stdin_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sink = Path(folder) / "sink"
            codes = rollout.run_pipeline(
                [sys.executable, "-c", "print('payload')"],
                [
                    sys.executable,
                    "-c",
                    f"import sys; open({str(sink)!r}, 'w').write(sys.stdin.read())",
                ],
            )
            assert codes == (0, 0, "")
            assert sink.read_text() == "payload\n"
            failed = rollout.run_pipeline(
                [sys.executable, "-c", "print('payload')"],
                [
                    sys.executable,
                    "-c",
                    "import sys; sys.stdin.read(); sys.exit('import refused')",
                ],
            )
            assert failed == (0, 1, "import refused")


class RecordRolloutTests(RecordRolloutCase):
    """Per-repo records go one way from the rollout source to remote omp devices."""

    def test_bundler_writer_and_lister_round_trip_on_a_real_target(self) -> None:
        home = self.scratch / "home"
        (home / ".omp").mkdir(parents=True)
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
        files = sorted(self.source)
        producer = [
            sys.executable,
            "-c",
            rollout.RECORDS_BUNDLER,
            str(self.base / "repos"),
            *files,
        ]
        bundle = subprocess.run(
            producer, capture_output=True, check=True, timeout=PROCESS_TIMEOUT
        )
        written = subprocess.run(
            [
                sys.executable,
                "-c",
                rollout.RECORDS_WRITER,
                "~/.omp",
                json.dumps(dict.fromkeys(files)),
            ],
            input=bundle.stdout,
            capture_output=True,
            env=env,
            check=False,
            timeout=PROCESS_TIMEOUT,
        )
        assert written.returncode == 0, written.stderr
        for relative in files:
            target = home / ".omp/repos" / relative
            assert target.read_bytes() == (self.base / "repos" / relative).read_bytes()
            assert target.stat().st_mode & 511 == 384
        listed = subprocess.run(
            [sys.executable, "-c", rollout.RECORDS_LISTER, "~/.omp"],
            capture_output=True,
            text=True,
            env=env,
            check=True,
            timeout=PROCESS_TIMEOUT,
        )
        listing = {
            key: tuple(value) for key, value in json.loads(listed.stdout).items()
        }
        assert listing == self.source, "the copy keeps source bytes and mtimes"
        assert rollout.record_plan(self.source, listing) == ([], [])
        self.write(home / ".omp", "example-owner/example-repo/gate.json", "{}\n", 2_000)
        listing = rollout.record_files(home / ".omp/repos")
        assert rollout.record_plan(self.source, listing) == (
            [],
            ["example-owner/example-repo"],
        )

    def test_writer_refuses_a_target_file_edited_after_inventory(self) -> None:
        home = self.scratch / "home"
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
        gate, profile = (
            "example-owner/example-repo/gate.json",
            "example-owner/example-repo/profile.md",
        )
        self.write(home / ".omp", gate, '{"old": 1}\n', 500)
        self.write(home / ".omp", profile, "old profile\n", 500)
        target = rollout.record_files(home / ".omp/repos")
        copy, newer = rollout.record_plan(self.source, target)
        assert (copy, newer) == ([gate, profile], []), (
            "the plan copies both older target files"
        )
        planned = json.dumps({relative: list(target[relative]) for relative in copy})
        edited = self.write(home / ".omp", gate, '{"edited": "after inventory"}\n', 500)
        bundle = subprocess.run(
            [
                sys.executable,
                "-c",
                rollout.RECORDS_BUNDLER,
                str(self.base / "repos"),
                *copy,
            ],
            capture_output=True,
            check=True,
            timeout=PROCESS_TIMEOUT,
        )
        written = subprocess.run(
            [sys.executable, "-c", rollout.RECORDS_WRITER, "~/.omp", planned],
            input=bundle.stdout,
            capture_output=True,
            text=False,
            env=env,
            check=False,
            timeout=PROCESS_TIMEOUT,
        )
        assert written.returncode == 5, written.stderr
        assert b"changed since inventory: " + gate.encode() in written.stderr
        assert edited.read_text() == '{"edited": "after inventory"}\n', (
            "the edited target stays untouched"
        )
        assert (home / ".omp/repos" / profile).read_bytes() == (
            self.base / "repos" / profile
        ).read_bytes(), "unchanged target files are still replaced"
        assert sorted(path.name for path in edited.parent.iterdir()) == [
            "gate.json",
            "profile.md",
        ], "no temporary file is left behind"

    def test_writer_refuses_paths_that_leave_the_records_root(self) -> None:
        home = self.scratch / "home"
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
        for relative in ("../escape", "/abs", "o/./n", ""):
            with self.subTest(relative=relative):
                document = json.dumps({relative: [1, "eA=="]}).encode()
                result = subprocess.run(
                    [sys.executable, "-c", rollout.RECORDS_WRITER, "~/.omp", "{}"],
                    input=document,
                    capture_output=True,
                    env=env,
                    check=False,
                    timeout=PROCESS_TIMEOUT,
                )
                assert result.returncode == 4
        assert not (self.scratch / "escape").exists()


if __name__ == "__main__":
    unittest.main()
