"""Consumer contracts using real checkouts, fake Podman/systemd and local HTTP."""

import base64
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from guardrails.host.process import run_text, start_logged

pytestmark = pytest.mark.seam

SOURCE = Path(__file__).resolve().parents[1]
# ci_gate.py finds records beside its own checkout, so tests run a copy inside a temp harness root.
HARNESS_FILES = (
    "scripts/ci_gate.py",
    "scripts/ci-gate-repos.json",
    "agent/lib/repo_record.py",
)
HARNESS: Path
SCRIPT: Path
PROCESS_TIMEOUT = 60
REPO = "adam-jackson-cf/oh-mi-pi-config"
TOKEN_ENV = "NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF"
# A context other than 'gate' proves the status context comes from the record's gate.json.
CONTEXT = "exact-head-gate"


def setUpModule() -> None:
    global HARNESS, SCRIPT
    HARNESS = Path(tempfile.mkdtemp(prefix="ci-gate-harness-")).resolve()
    for name in HARNESS_FILES:
        (HARNESS / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SOURCE / name, HARNESS / name)
    SCRIPT = HARNESS / "scripts/ci_gate.py"


def tearDownModule() -> None:
    shutil.rmtree(HARNESS, ignore_errors=True)


def mentions(call: list[str], text: str) -> bool:
    return any(text in arg for arg in call)


FAKE = r"""#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_ROOT'])
with (root / (name + '.jsonl')).open('a') as out:
    out.write(json.dumps(args) + '\n')
if name == 'git':
    config = {key: value for key, value in os.environ.items() if key.startswith('GIT_CONFIG_')}
    with (root / 'git-env.jsonl').open('a') as out:
        out.write(json.dumps({'args': args, 'config': config}) + '\n')
    if 'fetch' in args and os.environ.get('FAKE_REQUIRED_AUTH'):
        if config.get('GIT_CONFIG_VALUE_0') != os.environ['FAKE_REQUIRED_AUTH']:
            sys.exit(128)
    args = [os.environ['FAKE_REPO'] if a.startswith('https://github.com/') else a for a in args]
    sys.exit(subprocess.call([os.environ['REAL_GIT'], *args]))
if name == 'systemd-run':
    (root / 'launched').write_text('yes')
    sys.exit(0)
if name == 'infisical':
    if os.environ.get('FAKE_INFISICAL_TOKEN'):
        os.environ['NUC_GATE_GITHUB_TOKEN_ADAM_JACKSON_CF'] = os.environ['FAKE_INFISICAL_TOKEN']
    sys.exit(subprocess.call(args[args.index('--') + 1:]))
if args[:2] == ['image', 'inspect']:
    print(os.environ.get('FAKE_INSPECT_WARNING', ''), file=sys.stderr)
    print(os.environ.get('FAKE_IMAGE_USER', ''))
    sys.exit(0)
if args[:2] == ['image', 'exists']:
    sys.exit(0 if (root / ('image-' + args[-1].split(':')[-1])).exists() else 1)
if args[0] == 'build':
    if os.environ.get('FAIL_BUILD'):
        sys.exit(1)
    image = args[args.index('-t') + 1]
    (root / ('image-' + image.split(':')[-1])).touch()
    sys.exit(0)
if args[0] == 'run':
    src = pathlib.Path(args[args.index('-v') + 1].removesuffix(':/work:rw'))
    marker = root / ('entered-' + src.parent.name)
    marker.touch()
    if os.environ.get('BLOCK_RUN'):
        while not (root / ('release-' + src.parent.name)).exists():
            time.sleep(.01)
    print('container gate output', flush=True)
    if int(os.environ.get('GATE_EXIT', '0')) in (125, 126, 127):
        sys.exit(int(os.environ['GATE_EXIT']))
    # Execute the real shell boundary; only Podman's bind mounts are translated.
    command = args[-1]
    for index, value in enumerate(args):
        if value == '-v':
            host, container, mode = args[index + 1].rsplit(':', 2)
            command = command.replace(container, host)
    sys.exit(subprocess.call(['bash', '-c', command], cwd=src))
sys.exit(0)
"""


class GateFixture(unittest.TestCase):
    """A temp source repo, fake git/Podman/systemd/Infisical on the CI_GATE_* paths and a local status API."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="ci-gate-test-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.source = self.root / "repo"
        self.source.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Gate Test")
        self.git("config", "user.email", "gate@example.invalid")
        (self.source / ".devcontainer").mkdir()
        self.record = HARNESS / "repos" / REPO
        shutil.rmtree(self.record, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.record, True)
        self.write_gate(
            self.gate(
                checks={
                    "commit": ["bash", "commit only.sh"],
                    "audit": ["bash", "audit script.sh"],
                    "ci": ["bash", "gate script.sh", "a'b"],
                }
            )
        )
        (self.source / ".devcontainer/devcontainer.json").write_text(
            json.dumps({
                "build": {"dockerfile": "Containerfile"},
                "postCreateCommand": ["echo", "setup words"],
            })
        )
        (self.source / ".devcontainer/Containerfile").write_text("FROM test-image\n")
        (self.source / "gate script.sh").write_text('exit "${GATE_EXIT:-0}"\n')
        (self.source / "audit script.sh").write_text('exit "${AUDIT_EXIT:-0}"\n')
        self.sha = self.commit()
        self.requests: list[dict[str, Any]] = []
        requests = self.requests
        self.final_status_started = threading.Event()
        self.release_final_status = threading.Event()
        self.release_final_status.set()
        self.final_status_code = 201
        gate = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                request = {
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(
                        self.rfile.read(int(self.headers["Content-Length"]))
                    ),
                }
                final = request["body"]["state"] != "pending"
                if final:
                    gate.final_status_started.set()
                    gate.release_final_status.wait(timeout=10)
                requests.append(request)
                self.send_response(gate.final_status_code if final else 201)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args: Any) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(self.release_final_status.set)
        binary = self.root / "bin"
        binary.mkdir()
        real_git = shutil.which("git")
        assert real_git is not None, "the tests drive a real git"
        for name in ("git", "podman", "systemd-run", "infisical"):
            file = binary / name
            file.write_text(FAKE)
            file.chmod(0o755)
        self.env = {
            **os.environ,
            "XDG_STATE_HOME": str(self.root / "state"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "FAKE_ROOT": str(self.root),
            "FAKE_REPO": str(self.source),
            "REAL_GIT": real_git,
            "CI_GATE_GITHUB_API": f"http://127.0.0.1:{server.server_port}",
            "CI_GATE_INFISICAL_PROJECT": "test-project",
            "CI_GATE_INFISICAL_ENV": "test-env",
            TOKEN_ENV: "test-super-secret-token",
        }
        for name in ("git", "podman", "systemd-run", "infisical"):
            self.env["CI_GATE_" + name.upper().replace("-", "_")] = str(binary / name)
        self.children: list[subprocess.Popen[bytes]] = []
        self.run_logs: dict[int, Path] = {}
        self.addCleanup(self.stop_children)

    def stop_children(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=PROCESS_TIMEOUT)

    def git(self, *args: str) -> str:
        return run_text(
            ["git", "-C", str(self.source), *args], timeout=PROCESS_TIMEOUT, check=True
        ).stdout.strip()

    @staticmethod
    def gate(**fields: object) -> dict[str, Any]:
        """A complete record gate.json; `fields` replace its defaults, None removes one."""
        gate: dict[str, Any] = {
            "schemaVersion": 2,
            "checks": {"commit": ["true"], "audit": ["true"], "ci": ["true"]},
            "ci": {"requiredContext": CONTEXT, "runner": "ci-gate"},
            "dynaflows": {"enabled": True},
        }
        gate.update(fields)
        return {key: value for key, value in gate.items() if value is not None}

    def write_gate(self, gate: dict[str, Any] | None) -> None:
        self.record.mkdir(parents=True, exist_ok=True)
        (self.record / "gate.json").write_text(json.dumps(gate))

    def commit(self) -> str:
        self.git("add", ".")
        self.git("commit", "--allow-empty", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def cli(
        self, *args: object, expected: int = 0, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        result = run_text(
            [sys.executable, str(SCRIPT), *map(str, args)],
            env=env or self.env,
            timeout=20,
        )
        assert result.returncode == expected, result.stdout + result.stderr
        return result

    def job(self, sha: str | None = None) -> Path:
        return self.root / "state/ci-gate/jobs" / REPO / (sha or self.sha)

    def submit(self, sha: str | None = None) -> subprocess.CompletedProcess[str]:
        return self.cli("submit", REPO, sha or self.sha)

    def calls(self, name: str) -> list[Any]:
        file = self.root / (name + ".jsonl")
        return (
            [json.loads(line) for line in file.read_text().splitlines()]
            if file.exists()
            else []
        )

    def start_run(
        self, sha: str, setting: tuple[str, str] = ("BLOCK_RUN", "1")
    ) -> subprocess.Popen[bytes]:
        log = self.root / f"run-{len(self.children)}.log"
        with log.open("wb") as output:
            child = start_logged(
                [sys.executable, str(SCRIPT), "run", str(self.job(sha))],
                output=output,
                env={**self.env, setting[0]: setting[1]},
            )
        self.children.append(child)
        self.run_logs[child.pid] = log
        return child

    def run_output(self, child: subprocess.Popen[bytes]) -> str:
        child.wait(timeout=10)
        return self.run_logs[child.pid].read_text()

    def until(self, predicate: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 10
        while not predicate():
            assert time.monotonic() < deadline, (
                "Timed out waiting for fake process handshake"
            )
            time.sleep(0.01)

    def fetch_environment(self, mode: str, header: str) -> dict[str, str]:
        """The submit environment for a fetch mode: the token from the shell, from Infisical or none."""
        environment = {**self.env}
        if mode != "shell-private":
            del environment[TOKEN_ENV]
        if mode == "infisical-private":
            environment["FAKE_INFISICAL_TOKEN"] = self.env[TOKEN_ENV]
        if mode != "public":
            environment["FAKE_REQUIRED_AUTH"] = header
        return environment


class CiGateTests(GateFixture):
    def test_rejects_unlisted_repositories_and_malformed_shas_before_execution(
        self,
    ) -> None:
        for repo, sha in [
            ("other/repo", self.sha),
            ("../repo", self.sha),
            (REPO, "A" * 40),
            (REPO, "a" * 39),
        ]:
            self.cli("submit", repo, sha, expected=1)
        assert self.calls("git") == []
        assert self.requests == []

    def test_submit_run_status_and_credentials_boundary(self) -> None:
        submitted = json.loads(self.submit().stdout)
        assert submitted["job"] == str(self.job())
        assert self.git("rev-parse", "HEAD") == self.sha
        assert (self.job() / "src/.git").is_dir()
        self.cli("run", self.job())
        result = json.loads(self.cli("status", REPO, self.sha).stdout)
        assert result["state"] == "success"
        assert result["sha"] == self.sha
        assert result["exitCode"] == 0
        assert result["context"] == CONTEXT
        assert json.loads((self.job() / "job.json").read_text())["context"] == CONTEXT
        assert not (self.job() / "src").exists()
        assert [r["body"]["state"] for r in self.requests] == ["pending", "success"]
        for request in self.requests:
            assert request["body"]["context"] == CONTEXT
            assert len(request["body"]["description"]) <= 140
            assert (
                request["headers"]["Authorization"] == "Bearer " + self.env[TOKEN_ENV]
            )
            assert {k.lower(): v for k, v in request["headers"].items()}[
                "x-github-api-version"
            ] == "2022-11-28"
            assert self.sha in request["path"]
        args, gate = [argv for argv in self.calls("podman") if argv[0] == "run"]
        assert "--network=none" not in args
        assert "--network=none" in gate
        mounted = args.index("-v") + 2
        assert gate[: mounted + 2] == [
            *args[:mounted],
            "--network=none",
            "--runtime-flag=network=none",
        ]
        assert "--runtime-flag=ignore-cgroups" in args
        assert args[args.index("--runtime") + 1] == "/usr/local/bin/runsc"
        build = next(argv for argv in self.calls("podman") if argv[0] == "build")
        assert "--runtime" not in build
        assert not any(arg.startswith("--runtime") for arg in build)
        assert "/usr/local/bin/runsc" not in build
        assert "echo 'setup words'" in args[-1]
        assert args[-1].endswith("exec bash 'audit script.sh'")
        assert gate[-1] == "exec bash 'gate script.sh' 'a'\"'\"'b'"
        assert "commit only.sh" not in json.dumps([args, gate])
        assert not any(arg.startswith("--userns") for arg in args + gate)
        assert "-e" not in args
        assert "--env-host" not in args
        assert (
            self.env[TOKEN_ENV]
            not in json.dumps(self.calls("podman")) + (self.job() / "log").read_text()
        )
        for argv in self.calls("git"):
            assert self.env[TOKEN_ENV] not in json.dumps(argv)
            assert not any("Authorization:" in arg for arg in argv)
        self.cli("logs", REPO, self.sha, "--tail", 1)
        self.cli("wait", REPO, self.sha, "--timeout", 0)
        before = len(self.calls("systemd-run"))
        assert json.loads(self.submit().stdout) == result
        assert len(self.calls("systemd-run")) == before

    def test_fetch_credentials_are_ephemeral_for_shell_and_infisical_tokens(
        self,
    ) -> None:
        token = self.env[TOKEN_ENV]
        header = (
            "Authorization: Basic "
            + base64.b64encode(("x-access-token:" + token).encode()).decode()
        )
        inherited_config = {
            key: value
            for key, value in self.env.items()
            if key.startswith("GIT_CONFIG_")
        }
        for mode in ("shell-private", "infisical-private", "public"):
            with self.subTest(mode=mode):
                (self.source / "fetch-mode").write_text(mode)
                sha = self.commit()
                self.cli("submit", REPO, sha, env=self.fetch_environment(mode, header))
                assert (
                    run_text(
                        ["git", "-C", str(self.job(sha) / "src"), "rev-parse", "HEAD"],
                        timeout=PROCESS_TIMEOUT,
                        check=True,
                    ).stdout.strip()
                    == sha
                )
                fetch = next(
                    call
                    for call in self.calls("git-env")
                    if "fetch" in call["args"] and call["args"][-1] == sha
                )
                if mode == "public":
                    assert fetch["config"] == inherited_config
                else:
                    assert fetch["config"] == {
                        **inherited_config,
                        "GIT_CONFIG_COUNT": "1",
                        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                        "GIT_CONFIG_VALUE_0": header,
                    }
                if mode != "shell-private":
                    assert all(
                        call[:5]
                        == ["run", "--projectId", "test-project", "--env", "test-env"]
                        for call in self.calls("infisical")
                    )
                    assert any(
                        call[call.index("--") + 1 :]
                        == [sys.executable, str(SCRIPT), "fetch", str(self.job(sha))]
                        for call in self.calls("infisical")
                    )
                if mode == "infisical-private":
                    assert (
                        self.requests[-1]["headers"]["Authorization"]
                        == "Bearer " + token
                    )
                mirror = self.root / "data/ci-gate/mirrors" / (REPO + ".git")
                config = (mirror / "config").read_text()
                assert "extraheader" not in config.lower()
                assert token not in config
                assert header not in config
                for call in self.calls("git"):
                    assert token not in json.dumps(call)
                    assert header not in json.dumps(call)
                    assert not mentions(call, "Authorization:")
                for call in self.calls("git-env"):
                    if "fetch" not in call["args"]:
                        assert call["config"] == inherited_config
                assert token not in (self.job(sha) / "log").read_text()
                assert header not in (self.job(sha) / "log").read_text()
        assert token not in json.dumps(self.calls("systemd-run"))

    def test_running_resubmit_and_wait_timeout_do_not_relaunch(self) -> None:
        self.submit()
        self.cli("wait", REPO, self.sha, "--timeout", 0, expected=2)
        self.submit()
        assert len(self.calls("systemd-run")) == 1
        assert (
            json.loads(self.cli("status", REPO, self.sha).stdout)["state"] == "running"
        )

    def test_missing_record_is_an_error_naming_the_rollout(self) -> None:
        shutil.rmtree(self.record)
        self.submit()
        self.cli("run", self.job(), expected=1)
        result = json.loads(self.cli("wait", REPO, self.sha, expected=1).stdout)
        assert result["state"] == "error"
        assert "No per-repo record for " + REPO in result["error"]
        assert "device_rollout.py" in (self.job() / "log").read_text()
        assert self.requests == [], "no status may go to a guessed context"

    def test_nonzero_gate_is_failure(self) -> None:
        self.submit()
        self.env["GATE_EXIT"] = "7"
        self.cli("run", self.job(), expected=1)
        result = json.loads(self.cli("wait", REPO, self.sha, expected=1).stdout)
        assert (result["state"], result["exitCode"]) == ("failure", 7)
        assert self.requests[-1]["body"]["state"] == "failure"
        assert json.loads(self.submit().stdout) == result
        assert len(self.calls("systemd-run")) == 1

    def test_failed_audit_is_a_cached_failure_and_skips_the_offline_gate(self) -> None:
        self.submit()
        self.env["AUDIT_EXIT"] = "3"
        self.cli("run", self.job(), expected=1)
        result = json.loads(self.cli("wait", REPO, self.sha, expected=1).stdout)
        assert (result["state"], result["exitCode"]) == ("failure", 3)
        assert result["error"] == "dependency audit failed"
        assert self.requests[-1]["body"]["state"] == "failure"
        runs = [argv for argv in self.calls("podman") if argv[0] == "run"]
        assert len(runs) == 1
        assert "--network=none" not in runs[0]
        assert json.loads(self.submit().stdout) == result
        assert len(self.calls("systemd-run")) == 1

    def test_numeric_image_user_owns_the_checkout_in_both_containers(self) -> None:
        for user, mapping in (
            ("1000:1000", "--userns=keep-id:uid=1000,gid=1000"),
            ("1001", "--userns=keep-id:uid=1001,gid=1001"),
        ):
            with self.subTest(user=user):
                (self.source / "image-user").write_text(user)
                sha = self.commit()
                self.submit(sha)
                before = len(self.calls("podman"))
                self.cli(
                    "run", self.job(sha), env={**self.env, "FAKE_IMAGE_USER": user}
                )
                runs = [
                    argv for argv in self.calls("podman")[before:] if argv[0] == "run"
                ]
                assert len(runs) == 2
                for argv in runs:
                    assert argv[argv.index(mapping) + 1] == "-v"

    def test_image_inspection_warnings_reach_the_job_log_not_the_user(self) -> None:
        (self.source / "image-user").write_text("warned")
        sha = self.commit()
        self.submit(sha)
        before = len(self.calls("podman"))
        self.cli(
            "run",
            self.job(sha),
            env={
                **self.env,
                "FAKE_IMAGE_USER": "1000",
                "FAKE_INSPECT_WARNING": "inspect warning text",
            },
        )
        runs = [argv for argv in self.calls("podman")[before:] if argv[0] == "run"]
        assert len(runs) == 2
        assert all("--userns=keep-id:uid=1000,gid=1000" in argv for argv in runs)
        assert "inspect warning text" in self.cli("logs", REPO, sha).stdout

    def test_resubmitting_infrastructure_error_recreates_checkout_and_runs_again(
        self,
    ) -> None:
        for stage in ("build", "bootstrap"):
            with self.subTest(stage=stage):
                (self.source / "retry-stage").write_text(stage)
                if stage == "bootstrap":
                    (self.source / ".devcontainer/devcontainer.json").write_text(
                        json.dumps({
                            "build": {"dockerfile": "Containerfile"},
                            "postCreateCommand": "test -f bootstrap-ready",
                        })
                    )
                sha = self.commit()
                self.submit(sha)
                launches = len(self.calls("systemd-run"))
                environment = {
                    **self.env,
                    **({"FAIL_BUILD": "1"} if stage == "build" else {}),
                }
                self.cli("run", self.job(sha), expected=1, env=environment)
                assert (
                    json.loads((self.job(sha) / "result.json").read_text())["state"]
                    == "error"
                )
                assert not (self.job(sha) / "src").exists()
                retried = json.loads(self.submit(sha).stdout)
                assert retried["state"] == "running"
                assert not (self.job(sha) / "result.json").exists()
                assert (self.job(sha) / "src/.git").is_dir()
                assert len(self.calls("systemd-run")) == launches + 1
                units = [
                    argv[argv.index("--unit") + 1] for argv in self.calls("systemd-run")
                ]
                assert units[-2] != units[-1]
                if stage == "bootstrap":
                    (self.job(sha) / "src/bootstrap-ready").touch()
                self.cli("run", self.job(sha))
                result = json.loads(self.cli("wait", REPO, sha).stdout)
                assert (result["state"], result["sha"]) == ("success", sha)
                assert [request["body"]["state"] for request in self.requests][-4:] == [
                    "pending",
                    "error",
                    "pending",
                    "success",
                ]

    def test_infrastructure_failures_are_errors(self) -> None:
        for failure in (
            "missing-ci",
            "missing-audit",
            "build",
            "podman",
            "bootstrap-string",
            "bootstrap-array",
            "named-user",
        ):
            with self.subTest(failure=failure):
                gate = self.gate(
                    checks={
                        "missing-ci": {"commit": ["true"], "audit": ["true"]},
                        "missing-audit": {"commit": ["true"], "ci": ["true"]},
                    }.get(
                        failure,
                        {
                            "commit": ["true"],
                            "audit": ["true"],
                            "ci": ["true", failure],
                        },
                    )
                )
                if failure == "missing-gate":
                    (self.record / "gate.json").unlink()
                else:
                    self.write_gate(gate)
                sha = self.commit()
                if failure.startswith("bootstrap-"):
                    post = (
                        "exit 7"
                        if failure.endswith("string")
                        else ["bash", "-c", "exit 9"]
                    )
                    (self.source / ".devcontainer/devcontainer.json").write_text(
                        json.dumps({
                            "build": {"dockerfile": "Containerfile"},
                            "postCreateCommand": post,
                        })
                    )
                    sha = self.commit()
                self.submit(sha)
                environment = {**self.env}
                if failure == "build":
                    environment["FAIL_BUILD"] = "1"
                if failure == "podman":
                    environment["GATE_EXIT"] = "125"
                if failure == "named-user":
                    environment["FAKE_IMAGE_USER"] = "bun"
                self.cli("run", self.job(sha), expected=1, env=environment)
                result = json.loads(self.cli("wait", REPO, sha, expected=1).stdout)
                assert result["state"] == "error"
                assert self.requests[-1]["body"]["state"] == "error"
                assert self.requests[-1]["body"]["context"] == CONTEXT
                if failure.startswith("bootstrap-"):
                    assert "Container bootstrap failed" in result["error"]
                if failure.startswith("missing-"):
                    assert (
                        "checks." + failure.removeprefix("missing-") in result["error"]
                    )
                if failure == "named-user":
                    assert result["error"] == "Container image user bun must be numeric"
                assert list(self.job(sha).glob("execute-*")) == []

    def test_gate_without_required_context_is_an_unpublished_error(self) -> None:
        for failure in (
            "missing-gate",
            "missing-context",
            "unknown-schema",
            "unknownField",
            "checks",
            "ownerOnlyPaths",
        ):
            with self.subTest(failure=failure):
                gate = {
                    "missing-context": self.gate(ci=None),
                    "unknown-schema": {"schemaVersion": 3},
                    "unknownField": self.gate(unknownField=3),
                    "ownerOnlyPaths": self.gate(ownerOnlyPaths=[]),
                    "checks": self.gate(checks={"ci": ["true"]}),
                }.get(failure)
                if failure == "missing-gate":
                    (self.record / "gate.json").unlink(missing_ok=True)
                else:
                    self.write_gate(gate)
                sha = self.commit()
                before = len(self.requests)
                self.submit(sha)
                assert "context" not in json.loads(
                    (self.job(sha) / "job.json").read_text()
                )
                self.cli("run", self.job(sha), expected=1)
                waited = self.cli("wait", REPO, sha, expected=1)
                result = json.loads(waited.stdout)
                assert result["state"] == "error"
                assert "ci.requiredContext" in result["publicationError"]
                if failure in ("unknownField", "checks", "ownerOnlyPaths"):
                    # The run's error names the field the strict record schema rejected.
                    assert (
                        "checks.commit" if failure == "checks" else failure
                    ) in result["error"]
                assert "ci.requiredContext" in waited.stderr
                assert len(self.requests) == before, (
                    "no status may go to a guessed context"
                )

    def test_submit_fails_before_job_state_without_infisical_settings(self) -> None:
        for name in ("CI_GATE_INFISICAL_PROJECT", "CI_GATE_INFISICAL_ENV"):
            with self.subTest(missing=name):
                environment = {
                    key: value for key, value in self.env.items() if key != name
                }
                result = self.cli("submit", REPO, self.sha, expected=1, env=environment)
                assert name + " not set" in result.stderr
                assert "device_rollout.py" in result.stderr
                assert not self.job().exists()
                assert self.calls("git") == []
                assert self.requests == []

    def test_publication_failure_preserves_gate_result_but_wait_fails(self) -> None:
        for failure in ("missing-token", "http-error"):
            with self.subTest(failure=failure):
                (self.source / "publication-failure").write_text(failure)
                sha = self.commit()
                self.submit(sha)
                environment = {**self.env}
                if failure == "missing-token":
                    del environment[TOKEN_ENV]
                    message = "Missing GitHub token"
                else:
                    self.final_status_code = 503
                    message = "GitHub status publication failed"
                result = self.cli("run", self.job(sha), expected=1, env=environment)
                assert message in result.stderr
                waited = self.cli("wait", REPO, sha, "--timeout", 0, expected=1)
                assert message in waited.stderr
                saved = json.loads(waited.stdout)
                assert saved["state"] == "success"
                assert message in saved["publicationError"]
                assert not (self.job(sha) / "src").exists()
                self.cli("status", REPO, sha, expected=1)
                self.cli("run", self.job(sha), expected=1)

    def test_resubmit_republishes_failed_final_status_without_rerunning_gate(
        self,
    ) -> None:
        for mode in ("shell", "infisical"):
            with self.subTest(mode=mode):
                (self.source / "publication-retry").write_text(mode)
                sha = self.commit()
                self.submit(sha)
                self.final_status_code = 503
                self.cli("run", self.job(sha), expected=1)
                saved = json.loads((self.job(sha) / "result.json").read_text())
                assert saved["state"] == "success"
                assert "GitHub status publication failed" in saved["publicationError"]
                self.cli("wait", REPO, sha, "--timeout", 0, expected=1)
                before = len(self.requests)
                launches = len(self.calls("systemd-run"))
                runs = len([call for call in self.calls("podman") if call[0] == "run"])
                self.final_status_code = 201
                environment = {**self.env}
                if mode == "infisical":
                    del environment[TOKEN_ENV]
                    environment["FAKE_INFISICAL_TOKEN"] = self.env[TOKEN_ENV]
                retried = json.loads(
                    self.cli("submit", REPO, sha, env=environment).stdout
                )
                assert len(self.requests) == before + 1, (
                    "resubmit must POST the cached final status again"
                )
                assert self.requests[-1]["body"]["state"] == "success"
                assert (
                    self.requests[-1]["body"]["description"]
                    == f"success: gate ({saved['durationSeconds']:.1f}s)"
                )
                assert "publicationError" not in retried
                assert retried == {
                    key: value
                    for key, value in saved.items()
                    if key != "publicationError"
                }
                assert (
                    json.loads((self.job(sha) / "result.json").read_text()) == retried
                )
                assert (
                    json.loads(self.cli("wait", REPO, sha, "--timeout", 0).stdout)
                    == retried
                )
                assert len(self.calls("systemd-run")) == launches
                assert (
                    len([call for call in self.calls("podman") if call[0] == "run"])
                    == runs
                )
                if mode == "infisical":
                    assert any(
                        call[call.index("--") + 1 :]
                        == [
                            sys.executable,
                            str(SCRIPT),
                            "publish",
                            str(self.job(sha)),
                            "success",
                            f"success: gate ({saved['durationSeconds']:.1f}s)",
                        ]
                        for call in self.calls("infisical")
                    )

    def test_wait_finishes_after_final_status_publication(self) -> None:
        for exit_code, state in ((0, "success"), (7, "failure"), (125, "error")):
            with self.subTest(state=state):
                (self.source / "publication-state").write_text(state)
                sha = self.commit()
                self.submit(sha)
                self.final_status_started.clear()
                self.release_final_status.clear()
                runner = self.start_run(sha, ("GATE_EXIT", str(exit_code)))
                try:
                    assert self.final_status_started.wait(timeout=10)
                    # The server has received the final POST but has not accepted it.
                    self.cli("wait", REPO, sha, "--timeout", 0, expected=2)
                    assert self.requests[-1]["body"]["state"] == "pending"
                    assert not (self.job(sha) / "result.json").exists()
                finally:
                    self.release_final_status.set()
                waited = self.cli(
                    "wait",
                    REPO,
                    sha,
                    "--timeout",
                    10,
                    expected=0 if state == "success" else 1,
                )
                assert json.loads(waited.stdout)["state"] == state
                assert self.requests[-1]["body"]["state"] == state
                assert self.run_output(runner) == ""
                assert runner.returncode == (0 if state == "success" else 1)

    def test_unchanged_dockerfile_reuses_image(self) -> None:
        self.submit()
        self.cli("run", self.job())
        (self.source / "new-file").write_text("changed source, same Dockerfile")
        sha = self.commit()
        self.submit(sha)
        self.cli("run", self.job(sha))
        assert len([a for a in self.calls("podman") if a[0] == "build"]) == 1
        images = [
            json.loads((self.job(s) / "result.json").read_text())["image"]
            for s in (self.sha, sha)
        ]
        assert images[0] == images[1]

    def test_global_slot_serializes_runs(self) -> None:
        self.submit()
        (self.source / "second").write_text("second")
        second = self.commit()
        self.submit(second)
        first = self.start_run(self.sha)
        self.until(lambda: (self.root / ("entered-" + self.sha)).exists())
        other = self.start_run(second)
        # The second process is alive but cannot enter podman before the first releases.
        self.until(lambda: (self.job(second) / "run.lock").exists())
        assert not (self.root / ("entered-" + second)).exists()
        with (
            (self.root / "state/ci-gate/locks/slot-0").open("a") as held_slot,
            pytest.raises(BlockingIOError),
        ):
            fcntl.flock(held_slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert len(list((self.root / "state/ci-gate/locks").glob("slot-*"))) == 1
        (self.root / ("release-" + self.sha)).touch()
        self.until(lambda: (self.root / ("entered-" + second)).exists())
        (self.root / ("release-" + second)).touch()
        assert self.run_output(first) == ""
        assert self.run_output(other) == ""
        assert (first.returncode, other.returncode) == (0, 0)

    def test_image_configuration_without_build(self) -> None:
        (self.source / ".devcontainer/devcontainer.json").write_text(
            json.dumps({"image": "example/pinned:tag"})
        )
        sha = self.commit()
        self.submit(sha)
        self.cli("run", self.job(sha))
        assert not any(a[0] == "build" for a in self.calls("podman"))
        assert (
            json.loads((self.job(sha) / "result.json").read_text())["image"]
            == "example/pinned:tag"
        )

    def test_sigterm_records_timeout_and_removes_container(self) -> None:
        self.submit()
        child = self.start_run(self.sha)
        self.until(lambda: (self.root / ("entered-" + self.sha)).exists())
        child.send_signal(signal.SIGTERM)
        child.communicate(timeout=10)
        assert child.returncode == 1
        result = json.loads((self.job() / "result.json").read_text())
        assert (result["state"], result["error"]) == ("error", "timeout")
        assert self.requests[-1]["body"]["state"] == "error"
        assert any(a[:2] == ["rm", "--force"] for a in self.calls("podman"))


if __name__ == "__main__":
    unittest.main()
