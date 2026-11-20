#!/usr/bin/env python3
"""Run allowlisted exact-revision CI gates in rootless Podman on the NUC.

The gate policy (checks.ci and ci.requiredContext) comes from the repo's personal per-repo record,
<harness root>/repos/<owner>/<name>/gate.json beside this checkout, never from the repo tree.
The device rollout copies records to every device.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import IO, Any, NoReturn, TextIO

# The harness's record resolver, located from this file.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent" / "lib"))
import repo_record

SELF = Path(__file__).resolve()


def executable(name: str) -> str:
    return os.environ.get("CI_GATE_" + name.upper().replace("-", "_"), name)


def root(kind: str) -> Path:
    default = "~/.local/state" if kind == "STATE" else "~/.local/share"
    return (
        Path(os.environ.get("XDG_" + kind + "_HOME", default)).expanduser() / "ci-gate"
    )


def repositories() -> dict[str, dict[str, str]]:
    allowlist: dict[str, dict[str, str]] = json.loads(
        Path(
            os.environ.get("CI_GATE_REPOS", SELF.with_name("ci-gate-repos.json"))
        ).read_text()
    )
    return allowlist


def validate(repo: str, sha: str) -> None:
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
        or any(p in (".", "..") for p in repo.split("/"))
        or repo not in repositories()
    ):
        raise RuntimeError("Repository is not allowlisted")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("SHA must be 40 lowercase hex characters")


def job_path(repo: str, sha: str) -> Path:
    validate(repo, sha)
    return root("STATE") / "jobs" / repo / sha


def save(path: Path, value: object) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load(path: Path) -> Any:
    return json.loads(path.read_text())


def record_gate(repo: str) -> dict[str, Any]:
    """Return the gate.json object of the allowlisted repo's per-repo record, validated strictly."""
    record = repo_record.record_dir(repo, SELF.parents[1])
    if not (record / repo_record.GATE_FILE).is_file():
        raise RuntimeError(
            f"No per-repo record for {repo}; run scripts/device_rollout.py to copy records "
            "to this device (no ci.requiredContext)"
        )
    try:
        return repo_record.load_gate(record)
    except repo_record.RecordError as exc:
        raise RuntimeError(
            f"Invalid record gate.json for {repo}: {exc} (no ci.requiredContext)"
        ) from None


def check_argv(policy: object, name: str) -> list[str]:
    checks = (
        policy.get("checks")
        if isinstance(policy, dict) and policy.get("schemaVersion") == 2
        else None
    )
    argv = checks.get(name) if isinstance(checks, dict) else None
    if (
        not isinstance(argv, list)
        or not argv
        or any(not isinstance(arg, str) or not arg.strip() for arg in argv)
    ):
        raise RuntimeError(f"Invalid gate.json checks.{name} argv")
    return argv


def required_context(policy: object) -> str:
    """Return the commit status context from record gate.json schema version 2: ci.requiredContext."""
    ci = (
        policy.get("ci")
        if isinstance(policy, dict) and policy.get("schemaVersion") == 2
        else None
    )
    context = ci.get("requiredContext") if isinstance(ci, dict) else None
    if not isinstance(context, str) or not context.strip():
        raise RuntimeError("Invalid gate.json ci.requiredContext")
    return context


@contextmanager
def lock(path: Path) -> Generator[None, None, None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


@contextmanager
def slot() -> Generator[None, None, None]:
    count = int(os.environ.get("CI_GATE_SLOTS", "1"))
    if count < 1:
        raise RuntimeError("CI_GATE_SLOTS must be positive")
    streams: list[TextIO] = []
    try:
        directory = root("STATE") / "locks"
        directory.mkdir(parents=True, exist_ok=True)
        streams = [(directory / f"slot-{i}").open("a") for i in range(count)]
        while True:
            for stream in streams:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    yield
                    return
                except BlockingIOError:
                    continue
            time.sleep(0.05)
    finally:
        for stream in streams:
            stream.close()


def repo_lock(repo: str) -> Path:
    return root("STATE") / "locks" / (repo.replace("/", "-") + ".lock")


def infisical() -> list[str]:
    """Return the Infisical prefix; device_rollout.py writes both settings into ci-runners.json."""
    settings = {
        name: os.environ.get(name, "").strip()
        for name in ("CI_GATE_INFISICAL_PROJECT", "CI_GATE_INFISICAL_ENV")
    }
    missing = [name for name, value in settings.items() if not value]
    if missing:
        raise RuntimeError(
            " and ".join(missing) + " not set; run scripts/device_rollout.py to write "
            "ci-runners.json from the device-rollout-hosts.json infisical setting"
        )
    return [
        executable("infisical"),
        "run",
        "--projectId",
        settings["CI_GATE_INFISICAL_PROJECT"],
        "--env",
        settings["CI_GATE_INFISICAL_ENV"],
        "--",
    ]


def publish(job: Path, state: str, description: str) -> None:
    metadata = load(job / "job.json")
    name = repositories()[metadata["repo"]]["tokenEnv"]
    token = os.environ.get(name)
    if not token:
        raise RuntimeError(f"Missing GitHub token environment variable: {name}")
    if not metadata.get("context"):
        raise RuntimeError(
            "No ci.requiredContext recorded for this job; check the repo's record gate.json"
        )
    body = json.dumps({
        "context": metadata["context"],
        "state": state,
        "description": description[:140],
    }).encode()
    url = os.environ.get("CI_GATE_GITHUB_API", "https://api.github.com").rstrip("/")
    url += f"/repos/{metadata['repo']}/statuses/{metadata['sha']}"
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ci-gate",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def pending(job: Path, log: TextIO) -> None:
    metadata = load(job / "job.json")
    if not metadata.get("context"):
        log.write("No ci.requiredContext recorded; pending status not published.\n")
        return
    if os.environ.get(repositories()[metadata["repo"]]["tokenEnv"]):
        publish(job, "pending", "Queued on NUC (0s)")
    else:
        result = run_logged(
            infisical()
            + [
                sys.executable,
                str(SELF),
                "publish",
                str(job),
                "pending",
                "Queued on NUC (0s)",
            ],
            log,
            check=False,
        )
        if result.returncode:
            log.write(
                "Pending status publication failed; run will retain a local result.\n"
            )


def fetch(job: Path) -> None:
    metadata = load(job / "job.json")
    repo, sha = metadata["repo"], metadata["sha"]
    if job.resolve() != job_path(repo, sha).resolve():
        raise RuntimeError("Job directory does not match configured state directory")
    mirror = root("DATA") / "mirrors" / (repo + ".git")
    mirror.parent.mkdir(parents=True, exist_ok=True)
    git = [executable("git")]
    with (job / "log").open("a") as log:
        if not mirror.exists():
            run_logged(git + ["init", "--bare", str(mirror)], log, check=True)
        environment = os.environ.copy()
        token = environment.get(repositories()[repo]["tokenEnv"])
        if token:
            credential = base64.b64encode(("x-access-token:" + token).encode()).decode()
            # Scope credentials to this GitHub fetch, never argv or saved config.
            environment.update(
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                GIT_CONFIG_VALUE_0="Authorization: Basic " + credential,
            )
        run_logged(
            git
            + [
                "--git-dir",
                str(mirror),
                "fetch",
                "--no-tags",
                f"https://github.com/{repo}.git",
                sha,
            ],
            log,
            env=environment,
            check=True,
        )


def republish(job: Path, repo: str, result: dict[str, Any]) -> bool:
    """Retry the final status publication a cached result failed; True once it is published."""
    state = result["state"]
    description = (
        f"{state}: {result.get('error') or 'gate'} ({result['durationSeconds']:.1f}s)"
    )
    try:
        if os.environ.get(repositories()[repo]["tokenEnv"]):
            publish(job, state, description)
        else:
            with (job / "log").open("a") as log:
                run_logged(
                    infisical()
                    + [
                        sys.executable,
                        str(SELF),
                        "publish",
                        str(job),
                        state,
                        description,
                    ],
                    log,
                    check=True,
                )
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        message = (
            str(error)
            if isinstance(error, RuntimeError)
            else "GitHub status publication failed"
        )
        print(message, file=sys.stderr)
        return False
    del result["publicationError"]
    save(job / "result.json", result)
    return True


def checkout(job: Path, repo: str, sha: str, log: TextIO) -> None:
    """Fetch the exact revision into the mirror, check it out and record the job's context."""
    mirror = root("DATA") / "mirrors" / (repo + ".git")
    if os.environ.get(repositories()[repo]["tokenEnv"]):
        fetch(job)
    else:
        run_logged(
            infisical() + [sys.executable, str(SELF), "fetch", str(job)],
            log,
            check=True,
        )
    git = [executable("git")]
    run_logged(
        git
        + ["clone", "--no-checkout", "--no-hardlinks", str(mirror), str(job / "src")],
        log,
        check=True,
    )
    run_logged(
        git + ["-C", str(job / "src"), "checkout", "--detach", sha], log, check=True
    )
    try:
        context = required_context(record_gate(repo))
        save(job / "job.json", {"repo": repo, "sha": sha, "context": context})
    except (OSError, RuntimeError) as error:
        # run() records the same failure as an error result.
        log.write(
            (
                str(error)
                if isinstance(error, RuntimeError)
                else "Unreadable record gate.json"
            )
            + "\n"
        )


def start_unit(job: Path, repo: str, sha: str, log: TextIO, *, retry: bool) -> None:
    """Publish the pending status and start the job's transient systemd unit."""
    try:
        pending(job, log)
    except (RuntimeError, OSError) as error:
        log.write(
            str(error)
            if isinstance(error, RuntimeError)
            else "Pending status publication failed"
        )
    unit = "ci-gate-" + repo.replace("/", "-") + "-" + sha[:12]
    if retry:
        # The previous transient unit may still be collecting.
        unit += "-retry-" + str(time.time_ns())
    command = [
        executable("systemd-run"),
        "--user",
        "--unit",
        unit,
        "--collect",
        "-p",
        "RuntimeMaxSec=" + os.environ.get("CI_GATE_TIMEOUT", "1200"),
        "-p",
        "TimeoutStopSec=45",
        "-p",
        "KillMode=mixed",
    ]
    # Transient units do not inherit SSH's environment. Forward service settings,
    # never credentials; Infisical owns the unit's secret injection.
    for key, value in os.environ.items():
        if (
            key.startswith("CI_GATE_")
            and key not in {entry["tokenEnv"] for entry in repositories().values()}
            or key in {"XDG_DATA_HOME", "XDG_STATE_HOME"}
        ):
            command.append("--setenv=" + key + "=" + value)
    run_logged(
        command + infisical() + [sys.executable, str(SELF), "run", str(job)],
        log,
        check=True,
    )


def submit(repo: str, sha: str) -> int:
    job = job_path(repo, sha)
    # Every submission launches its unit through Infisical: fail before any job state exists.
    infisical()
    job.mkdir(parents=True, exist_ok=True)
    with lock(job / "submit.lock"):
        retry = False
        if (job / "result.json").exists():
            result = load(job / "result.json")
            if result["state"] != "error":
                if result.get("publicationError") and not republish(job, repo, result):
                    return 1
                print(json.dumps(result))
                return 0
            # Wait for the previous run to release its lock before replacing its result.
            with lock(job / "run.lock"):
                shutil.rmtree(job / "src", ignore_errors=True)
                (job / "result.json").unlink()
                (job / "job.json").unlink(missing_ok=True)
            retry = True
        if (job / "job.json").exists():
            print(json.dumps({"state": "running", "job": str(job), "sha": sha}))
            return 0
        save(job / "job.json", {"repo": repo, "sha": sha})
        try:
            with (job / "log").open("a") as log, lock(repo_lock(repo)):
                checkout(job, repo, sha, log)
            with (job / "log").open("a") as log:
                start_unit(job, repo, sha, log, retry=retry)
        except (OSError, subprocess.SubprocessError):
            failed = {
                "repo": repo,
                "sha": sha,
                "image": None,
                "exitCode": 1,
                "state": "error",
                "startedAt": now(),
                "finishedAt": now(),
                "durationSeconds": 0,
                "error": "submission failed",
            }
            save(job / "result.json", failed)
            shutil.rmtree(job / "src", ignore_errors=True)
            raise RuntimeError(
                "Submission failed; inspect job log: " + str(job)
            ) from None
    print(json.dumps({"state": "running", "job": str(job), "sha": sha}))
    return 0


def now() -> str:
    return datetime.now(UTC).isoformat()


def runtime_flags(*, build: bool = False) -> list[str]:
    # NUC Podman 6.1.4 passes --no-new-keyring during builds; runsc rejects it.
    # Build only the owner's allowlisted repositories' Containerfiles with
    # Podman's default runtime. Actual gate execution stays inside gVisor.
    key = "CI_GATE_BUILD_RUNTIME_FLAGS" if build else "CI_GATE_RUNTIME_FLAGS"
    default = (
        []
        if build
        else ["--runtime", "/usr/local/bin/runsc", "--runtime-flag=ignore-cgroups"]
    )
    flags: list[str] = json.loads(os.environ.get(key, json.dumps(default)))
    return flags


def offline_flags(flags: list[str]) -> list[str]:
    sandboxed = any("runsc" in flag for flag in flags)
    return ["--network=none", *(["--runtime-flag=network=none"] if sandboxed else [])]


def checkout_file(src: Path, relative: str) -> Path:
    path = (src / relative).resolve()
    if not path.is_relative_to(src.resolve()):
        raise RuntimeError("Configuration path escapes checkout")
    return path


def run(job: Path) -> int:
    metadata = load(job / "job.json")
    validate(metadata["repo"], metadata["sha"])
    if job.resolve() != job_path(metadata["repo"], metadata["sha"]).resolve():
        raise RuntimeError("Job directory does not match configured state directory")
    with lock(job / "run.lock"):
        if (job / "result.json").exists():
            result = load(job / "result.json")
            return (
                0
                if result["state"] == "success" and not result.get("publicationError")
                else 1
            )
        return execute(job, metadata)


def run_logged(
    argv: list[str],
    log: TextIO | tuple[IO[bytes], TextIO],
    *,
    check: bool,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    stdout, stderr = log if isinstance(log, tuple) else (log, log)
    return subprocess.run(
        argv,
        stdout=stdout,
        stderr=stderr,
        env=env,
        timeout=timeout,
        check=check,
    )


def container_command(argv: list[str], log: TextIO) -> int:
    process = subprocess.Popen(argv, stdout=log, stderr=log)
    try:
        return process.wait()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise


@contextmanager
def container_lifetime(
    podman: str, container: str, log: TextIO
) -> Generator[None, None, None]:
    try:
        yield
    finally:
        # Keep the global slot until an interrupted container is stopped.
        run_logged([podman, "rm", "--force", container], log, timeout=30, check=False)


@dataclass
class Attempt:
    """One gate execution's outcome so far; the result record is built from it whatever happens."""

    job: Path
    metadata: dict[str, Any]
    image: object = None
    state: str = "error"
    code: int = 1
    reason: str | None = None


def terminated(signum: int, frame: FrameType | None) -> NoReturn:
    raise RuntimeError("timeout")


def post_create(config: dict[str, Any]) -> str:
    """The devcontainer's postCreateCommand as one shell command ('' when there is none)."""
    post = config.get("postCreateCommand", "")
    if isinstance(post, list) and all(isinstance(arg, str) for arg in post):
        post = shlex.join(post)
    if not isinstance(post, str):
        raise RuntimeError("Invalid postCreateCommand")
    return post


def configured_image(
    src: Path, config: dict[str, Any], repo: str
) -> tuple[object, Path | None]:
    """The gate image the devcontainer names, with the Containerfile that builds it (None for a pulled image)."""
    if "build" in config:
        dockerfile = checkout_file(
            src, ".devcontainer/" + config["build"]["dockerfile"]
        )
        digest = hashlib.sha256(dockerfile.read_bytes()).hexdigest()[:12]
        return "localhost/ci-gate/" + repo.replace("/", "-") + ":" + digest, dockerfile
    return config["image"], None


def prepare_image(
    podman: str, image: object, dockerfile: Path | None, log: TextIO
) -> str:
    """Check the image name and build a Containerfile image that does not exist yet."""
    if not isinstance(image, str) or not image or image.startswith("-"):
        raise RuntimeError("Invalid container image")
    if dockerfile is not None:
        exists = container_command([podman, "image", "exists", image], log)
        if exists == 1:
            built = container_command(
                [
                    podman,
                    "build",
                    *runtime_flags(build=True),
                    "-t",
                    image,
                    "-f",
                    str(dockerfile),
                    str(dockerfile.parent),
                ],
                log,
            )
            if built:
                raise RuntimeError("Image build failed")
        elif exists:
            raise RuntimeError("Podman image lookup failed")
    return image


def bootstrap_command(post: str, audit: list[str]) -> str:
    return ((post + "\n") if post else "") + (
        "bootstrap_status=$?\n"
        'if (( bootstrap_status )); then exit "$bootstrap_status"; fi\n'
        ": > /ci-gate-state/bootstrapped && exec " + shlex.join(audit)
    )


def container_exit(
    job: Path, run_argv: list[str], image: str, command: str, log: TextIO
) -> tuple[int, bool]:
    with tempfile.TemporaryDirectory(prefix="execute-", dir=job) as phase:
        code = container_command(
            [
                *run_argv,
                "-v",
                phase + ":/ci-gate-state:rw",
                "-w",
                "/work",
                image,
                "bash",
                "-c",
                command,
            ],
            log,
        )
        if code in {125, 126, 127} or code < 0:
            raise RuntimeError("Podman execution failed")
        return code, (Path(phase) / "bootstrapped").is_file()


def user_mapping(podman: str, image: str, log: TextIO) -> list[str]:
    with tempfile.TemporaryFile() as output:
        inspected = run_logged(
            [podman, "image", "inspect", "--format", "{{.Config.User}}", image],
            (output, log),
            timeout=60,
            check=False,
        )
        output.seek(0)
        user = output.read().decode().strip()
    if inspected.returncode:
        raise RuntimeError("Podman image inspection failed")
    uid, _, gid = user.partition(":")
    if user in {"", "root", "0", "0:0"}:
        return []
    if not re.fullmatch(r"[0-9]+", uid) or not re.fullmatch(r"[0-9]*", gid):
        raise RuntimeError(f"Container image user {user} must be numeric")
    return [f"--userns=keep-id:uid={uid},gid={gid or uid}"]


def run_gate(attempt: Attempt, podman: str, container: str, log: TextIO) -> None:
    """Resolve the record's gate, prepare the image and run the gate, recording each step on ``attempt``."""
    src = attempt.job / "src"
    policy = record_gate(attempt.metadata["repo"])
    attempt.metadata = {**attempt.metadata, "context": required_context(policy)}
    save(attempt.job / "job.json", attempt.metadata)
    audit = check_argv(policy, "audit")
    gate = check_argv(policy, "ci")
    config = load(checkout_file(src, ".devcontainer/devcontainer.json"))
    post = post_create(config)
    with lock(repo_lock(attempt.metadata["repo"])):
        attempt.image, dockerfile = configured_image(
            src, config, attempt.metadata["repo"]
        )
        image = prepare_image(podman, attempt.image, dockerfile, log)
    run_argv = [
        podman,
        "run",
        "--rm",
        "--name",
        container,
        *runtime_flags(),
        *user_mapping(podman, image, log),
        "-v",
        str(src) + ":/work:rw",
    ]
    attempt.code, bootstrapped = container_exit(
        attempt.job, run_argv, image, bootstrap_command(post, audit), log
    )
    if not bootstrapped:
        raise RuntimeError(f"Container bootstrap failed (exit {attempt.code})")
    if attempt.code:
        attempt.state, attempt.reason = "failure", "dependency audit failed"
        return
    attempt.code, _ = container_exit(
        attempt.job,
        [*run_argv, *offline_flags(runtime_flags())],
        image,
        "exec " + shlex.join(gate),
        log,
    )
    attempt.state = "success" if attempt.code == 0 else "failure"


def finish(job: Path, result: dict[str, Any], description: str) -> int:
    """Publish the final status and save the result; 0 only for a published success."""
    try:
        publish(job, result["state"], description)
    except (RuntimeError, OSError) as error:
        message = (
            str(error)
            if isinstance(error, RuntimeError)
            else "GitHub status publication failed"
        )
        result["publicationError"] = message
        print(message, file=sys.stderr)
        with (job / "log").open("a") as log:
            log.write(message + "\n")
    finally:
        # A completed result means both the gate and final publication have finished.
        save(job / "result.json", result)
    return (
        0 if result["state"] == "success" and not result.get("publicationError") else 1
    )


def execute(job: Path, metadata: dict[str, Any]) -> int:
    start, started = time.monotonic(), now()
    attempt = Attempt(job, metadata)
    container = (
        "ci-gate-" + metadata["repo"].replace("/", "-") + "-" + metadata["sha"][:12]
    )
    podman = executable("podman")
    previous = signal.signal(signal.SIGTERM, terminated)
    try:
        with (job / "log").open("a", buffering=1) as log:
            try:
                with slot(), container_lifetime(podman, container, log):
                    run_gate(attempt, podman, container, log)
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
                subprocess.SubprocessError,
            ) as error:
                attempt.reason = (
                    str(error)
                    if isinstance(error, RuntimeError)
                    else "Infrastructure failure (" + type(error).__name__ + ")"
                )
                log.write(attempt.reason + "\n")
                attempt.state, attempt.code = "error", 1
    except (OSError, subprocess.SubprocessError):
        attempt.state, attempt.code, attempt.reason = (
            "error",
            1,
            "Container cleanup failed",
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
        duration = round(time.monotonic() - start, 3)
        result = {
            **attempt.metadata,
            "image": attempt.image,
            "exitCode": attempt.code,
            "state": attempt.state,
            "startedAt": started,
            "finishedAt": now(),
            "durationSeconds": duration,
        }
        if attempt.reason:
            result["error"] = attempt.reason
        shutil.rmtree(job / "src", ignore_errors=True)
    return finish(
        job, result, f"{attempt.state}: {attempt.reason or 'gate'} ({duration:.1f}s)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("submit", "status", "wait", "logs"):
        command = commands.add_parser(name)
        command.add_argument("repo")
        command.add_argument("sha")
        if name == "wait":
            command.add_argument("--timeout", type=float, default=1200)
        if name == "logs":
            command.add_argument("--tail", type=int)
    commands.add_parser("run").add_argument("job", type=Path)
    commands.add_parser("fetch").add_argument("job", type=Path)
    command = commands.add_parser("publish")
    command.add_argument("job", type=Path)
    command.add_argument("state")
    command.add_argument("description")
    return parser


def show_logs(job: Path, tail: int | None) -> None:
    with (job / "log").open() as stream:
        if tail is None:
            shutil.copyfileobj(stream, sys.stdout)
        elif tail:
            sys.stdout.writelines(deque(stream, maxlen=tail))


def await_result(job: Path, command: str, timeout: float) -> int:
    """Print the job's result (status, or wait until ``timeout``); exit 0 success, 1 failure, 2 timeout."""
    deadline = time.monotonic() + timeout
    while True:
        if (job / "result.json").exists():
            result = load(job / "result.json")
            print(json.dumps(result))
            if result.get("publicationError"):
                print(result["publicationError"], file=sys.stderr)
                return 1
            return 0 if result["state"] == "success" else 1
        if command == "status":
            if not (job / "job.json").exists():
                raise RuntimeError("Unknown job")
            print(json.dumps({"state": "running", "job": str(job)}))
            return 0
        if time.monotonic() >= deadline:
            print("Timed out waiting for " + str(job), file=sys.stderr)
            return 2
        time.sleep(0.1)


def main() -> int:
    os.umask(0o077)
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "submit":
        return submit(args.repo, args.sha)
    if args.command == "run":
        return run(args.job)
    if args.command == "fetch":
        fetch(args.job)
    elif args.command == "publish":
        publish(args.job, args.state, args.description)
    elif args.command == "logs":
        job = job_path(args.repo, args.sha)
        if args.tail is not None and args.tail < 0:
            parser.error("--tail must be nonnegative")
        show_logs(job, args.tail)
    else:
        return await_result(
            job_path(args.repo, args.sha), args.command, getattr(args, "timeout", 0)
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        # Never print subprocess argv/output or HTTP exception details: authentication
        # headers may be present. Repository-controlled output is only in the job log.
        print(
            str(error)
            if isinstance(error, RuntimeError)
            else "CI gate operation failed; inspect the job log.",
            file=sys.stderr,
        )
        sys.exit(1)
