import json
import sys
import tempfile
import unittest
from pathlib import Path

import pytest
from test_device_rollout import HOSTS, MODULE, PIN, rollout

from guardrails.host.process import run_text

pytestmark = pytest.mark.seam


PROCESS_TIMEOUT = 60


class RunnerConfigTests(unittest.TestCase):
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
                pin=PIN,
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
            result = run_text(
                [sys.executable, str(cli), "submit", "github-actions", "o/r", "a" * 40],
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
            result = run_text(
                [
                    sys.executable,
                    "-c",
                    rollout.RUNNERS_WRITER,
                    "apply",
                    "~/.omp/ci-runners.json",
                    document,
                ],
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
        result = run_text(
            [
                sys.executable,
                "-c",
                rollout.CREDENTIAL_BUILDER,
                "FAKE_JUDGE_KEY",
                "typesafe",
            ],
            env=env,
            check=False,
            timeout=PROCESS_TIMEOUT,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {
            "version": 1,
            "credentials": [
                {
                    "provider": "typesafe",
                    "credential": {"type": "api_key", "key": "fake-value"},
                }
            ],
        }
        missing = run_text(
            [
                sys.executable,
                "-c",
                rollout.CREDENTIAL_BUILDER,
                "FAKE_JUDGE_KEY",
                "typesafe",
            ],
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


if __name__ == "__main__":
    unittest.main()
