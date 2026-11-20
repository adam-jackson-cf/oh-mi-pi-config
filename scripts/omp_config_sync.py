#!/usr/bin/env python3
"""Deploy tracked NUC configuration without mirroring private runtime state."""

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any, NoReturn

BUN_VERSION = "1.4.4"
CORRUPT_STATE = "Deployment state is unreadable or corrupt; restore deployment.json from the last known deployment"
SYNC_FAILED = "Configuration sync failed or timed out; verify dependencies and local service status, then retry."

Tracked = dict[str, tuple[bytes, int]]
Hashes = dict[str, str]
State = dict[str, Any]


def git(source: Path, *args: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(source), *args], stderr=subprocess.PIPE, timeout=120
    )


def head(source: Path) -> str:
    return git(source, "rev-parse", "HEAD").decode().strip()


def tracked(source: Path, revision: str) -> Tracked:
    entries = {}
    for record in git(source, "ls-tree", "-rz", revision).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        path = os.fsdecode(name)
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise RuntimeError(f"Unsupported tracked file: {path}")
        if not safe_path(path):
            raise RuntimeError(f"Unsafe tracked path: {path}")
        entries[path] = (
            git(source, "cat-file", "blob", oid.decode()),
            int(mode, 8) & 0o777,
        )
    return entries


def destination(runtime: Path, name: str) -> Path:
    path = runtime / name
    for component in [path, *path.parents]:
        if component == runtime.parent:
            break
        if component.is_symlink():
            raise RuntimeError(f"Refusing runtime symlink: {name}")
    return path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_revision(source: Path, revision: object) -> None:
    if not isinstance(revision, str) or not re.fullmatch(
        r"[0-9a-f]{40}|[0-9a-f]{64}", revision
    ):
        raise RuntimeError(
            "Deployment state has an invalid revision; restore the last deployed commit in deployment.json"
        )
    try:
        git(source, "cat-file", "-e", revision + "^{commit}")
    except subprocess.CalledProcessError:
        raise RuntimeError(
            "Previous deployed revision is unavailable; fetch its history or restore the correct deployment.json"
        ) from None


def safe_path(name: object) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and not Path(name).is_absolute()
        and ".." not in Path(name).parts
        and ".git" not in Path(name).parts
    )


def valid_hashes(hashes: object) -> bool:
    return isinstance(hashes, dict) and all(
        safe_path(name)
        and isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value) is not None
        for name, value in hashes.items()
    )


def read_state(state_file: Path) -> State:
    try:
        state = json.loads(state_file.read_text())
    except (ValueError, OSError):
        raise RuntimeError(CORRUPT_STATE) from None
    if (
        not isinstance(state, dict)
        or "revision" not in state
        or not valid_hashes(state.get("hashes", {}))
    ):
        raise RuntimeError(CORRUPT_STATE)
    return state


def load_state(source: Path, state_file: Path) -> State:
    if not state_file.exists():
        return {"revision": head(source)}
    state = read_state(state_file)
    validate_revision(source, state["revision"])
    return state


def save_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("w") as stream:
        stream.write(json.dumps(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def save_state(state_file: Path, revision: str, hashes: Hashes) -> None:
    save_json(state_file, {"revision": revision, "hashes": hashes})


def load_pending(source: Path, pending_file: Path) -> State:
    pending = load_state(source, pending_file)
    validate_revision(source, pending.get("previous_revision"))
    if head(source) != pending["revision"]:
        raise RuntimeError(
            "Pending deployment target differs from source HEAD; restore HEAD to the pending revision before retrying"
        )
    return pending


def runtime_hashes(source: Path, runtime: Path) -> Hashes:
    return {
        name: digest(destination(runtime, name).read_bytes())
        for name in tracked(source, "HEAD")
        if destination(runtime, name).is_file()
    }


@dataclass(frozen=True)
class Deployment:
    source: Path
    runtime: Path
    backups: Path
    journal: Path | None = None


@dataclass(frozen=True)
class Release:
    runtime: Path
    current: Tracked
    previous: Tracked
    expected: Hashes
    recovering: bool
    retired: set[str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "retired", self.previous.keys() - self.current.keys())

    def changed(self) -> list[str]:
        return [
            name
            for name in self.current.keys() | self.retired
            if self.needs_write(name)
        ]

    def needs_write(self, name: str) -> bool:
        path = destination(self.runtime, name)
        if path.exists() and not path.is_file():
            raise RuntimeError(f"Runtime destination is not a file: {name}")
        if name in self.retired:
            return self.retired_present(name, path)
        if not path.exists():
            return True
        self.verify(name, path)
        return not self.unchanged(name, path)

    def retired_present(self, name: str, path: Path) -> bool:
        if path.exists() and path.read_bytes() != self.previous[name][0]:
            raise RuntimeError(f"Locally modified retired file preserved: {name}")
        return path.exists()

    def verify(self, name: str, path: Path) -> None:
        expected = self.expected.get(
            name, digest(self.previous.get(name, self.current[name])[0])
        )
        actual = digest(path.read_bytes())
        if actual != expected and not (
            self.recovering and actual == digest(self.current[name][0])
        ):
            raise RuntimeError(f"Locally modified tracked file preserved: {name}")

    def unchanged(self, name: str, path: Path) -> bool:
        # Preserve the managed overlay when the corresponding source did not change.
        if name in self.previous and self.current[name] == self.previous[name]:
            return True
        data, mode = self.current[name]
        return path.read_bytes() == data and path.stat().st_mode & 0o777 == mode


def back_up(runtime: Path, backup_root: Path, changed: list[str]) -> None:
    existing = [name for name in changed if destination(runtime, name).is_file()]
    if not existing:
        return
    backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup_root.chmod(0o700)
    backup = Path(tempfile.mkdtemp(prefix="deployment-", dir=backup_root))
    for name in existing:
        saved = backup / name
        saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        saved.write_bytes(destination(runtime, name).read_bytes())
        saved.chmod(0o600)


def retire(runtime: Path, path: Path) -> None:
    path.unlink()
    parent = path.parent
    while parent != runtime:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def install(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
        temporary.write(data)
        temporary_path = Path(temporary.name)
    temporary_path.chmod(mode)
    temporary_path.replace(path)


def deploy(
    deployment: Deployment,
    previous_revision: str,
    expected_hashes: Hashes | None = None,
    recovering: bool = False,
) -> str:
    """Refuse runtime divergence, back up changed files and deploy source changes."""
    source, runtime = deployment.source, deployment.runtime
    validate_revision(source, previous_revision)
    target_revision = head(source)
    expected_hashes = expected_hashes or {}
    release = Release(
        runtime,
        tracked(source, target_revision),
        tracked(source, previous_revision),
        expected_hashes,
        recovering,
    )
    changed = release.changed()
    if head(source) != target_revision:
        raise RuntimeError(
            "Source HEAD changed during deployment; restore the pinned revision before retrying"
        )
    if deployment.journal is not None and not recovering:
        # This records only known source/managed hashes, never arbitrary runtime bytes.
        save_json(
            deployment.journal,
            {
                "revision": target_revision,
                "previous_revision": previous_revision,
                "hashes": expected_hashes,
            },
        )
    back_up(runtime, deployment.backups, changed)
    for name in changed:
        path = destination(runtime, name)
        if name in release.retired:
            retire(runtime, path)
        else:
            install(path, *release.current[name])
    return target_revision


def dependencies() -> tuple[Path, Path]:
    bun = Path.home() / f".local/share/mise/installs/bun/{BUN_VERSION}/bin/bun"
    overlay = Path.home() / ".local/bin/agent-introspection"
    for executable in (bun, overlay):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise RuntimeError(
                f"Required executable is missing or not executable: {executable.name}"
            )
    if not shutil.which("git"):
        raise RuntimeError("Required executable is missing: git")
    version = subprocess.check_output(
        [str(bun), "--version"], stderr=subprocess.PIPE, timeout=15
    )
    if version.decode().strip() != BUN_VERSION:
        raise RuntimeError(f"Install Bun {BUN_VERSION} in mise before syncing")
    return bun, overlay


def arguments() -> tuple[Path, Path, Path]:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path.home() / "Projects/tools/omp-nuc-config"
    )
    parser.add_argument("--runtime", type=Path, default=Path.home() / ".omp")
    parser.add_argument(
        "--state", type=Path, default=Path.home() / ".local/state/omp-config-sync"
    )
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("This deployment entry point is Linux NUC only")
    os.umask(0o077)
    source, runtime, state = (
        path.expanduser().resolve() for path in (args.source, args.runtime, args.state)
    )
    if runtime != (Path.home() / ".omp").resolve():
        raise RuntimeError(
            "Runtime must be ~/.omp; the CLI does not support staging because telemetry configures the home runtime"
        )
    if source == runtime or source in runtime.parents or runtime in source.parents:
        parser.error("Source and runtime must be separate directories")
    return source, runtime, state


def check_source(source: Path) -> None:
    if git(source, "branch", "--show-current").decode().strip() != "nuc-config":
        raise RuntimeError("Source must be on nuc-config")
    if git(source, "status", "--porcelain").strip():
        raise RuntimeError("Source checkout must be clean")


def baseline(source: Path, state: Path) -> tuple[str, Hashes, bool]:
    state_file = state / "deployment.json"
    # A pending journal pins recovery before any network operation can move HEAD.
    pending_file = state / "pending.json"
    if pending_file.exists():
        pending = load_pending(source, pending_file)
        return pending["previous_revision"], pending.get("hashes", {}), True
    previous = load_state(source, state_file)
    if not state_file.exists():
        save_state(state_file, previous["revision"], {})
    subprocess.run(
        ["git", "-C", str(source), "pull", "--ff-only", "origin", "nuc-config"],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return previous["revision"], previous.get("hashes", {}), False


def configure(runtime: Path, bun: Path, overlay: Path) -> None:
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join([
        str(bun.parent),
        str(Path.home() / ".local/share/mise/shims"),
        str(Path.home() / ".local/bin"),
        environment.get("PATH", ""),
    ])
    environment["SHARP_IGNORE_GLOBAL_LIBVIPS"] = "1"
    subprocess.run(
        [str(bun), "install", "--frozen-lockfile"],
        cwd=runtime,
        env=environment,
        check=True,
        timeout=600,
    )
    subprocess.run(
        [str(overlay), "harness", "configure", "oh-my-pi"],
        env=environment,
        check=True,
        capture_output=True,
        timeout=120,
    )


def synchronize(source: Path, runtime: Path, state: Path) -> None:
    bun, overlay = dependencies()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)
    with (state / "sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another configuration sync is running") from None
        check_source(source)
        state_file = state / "deployment.json"
        pending_file = state / "pending.json"
        previous_revision, expected, recovering = baseline(source, state)
        deployment = Deployment(source, runtime, state / "backups", pending_file)
        revision = deploy(deployment, previous_revision, expected, recovering)
        save_state(state_file, revision, runtime_hashes(source, runtime))
        pending_file.unlink()
        try:
            configure(runtime, bun, overlay)
        except BaseException:
            # A failed overlay may still have changed tracked bytes. Record only hashes,
            # so retries accept changes made by this managed command, never secret values.
            with contextlib.suppress(OSError, RuntimeError, subprocess.SubprocessError):
                save_state(state_file, revision, runtime_hashes(source, runtime))
            raise
        save_state(state_file, revision, runtime_hashes(source, runtime))
        print("NUC configuration synchronized; restart OMP sessions to load changes.")


def main() -> None:
    synchronize(*arguments())


def terminated(signum: int, frame: FrameType | None) -> NoReturn:
    raise RuntimeError("Configuration sync interrupted; retry to recover pending work")


def run() -> int:
    signal.signal(signal.SIGTERM, terminated)
    try:
        main()
    except (
        RuntimeError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
        ValueError,
    ) as error:
        # Do not include command output or exception details that could contain credentials.
        print(
            str(error) if isinstance(error, RuntimeError) else SYNC_FAILED,
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
