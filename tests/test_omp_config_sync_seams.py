import fcntl
import json
import shlex
import signal
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from test_omp_config_sync import sync

from guardrails.host.process import run_bytes

pytestmark = pytest.mark.seam

PROCESS_TIMEOUT = 60


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source"
        self.runtime = self.root / ".omp"
        self.state = self.root / "state"
        self.source.mkdir()
        self.runtime.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
        self.write("agent/skills/workflow-templates/SKILL.md", b"old workflow")
        self.write("agent/config.yml", b"old config")
        self.commit()
        self.previous = self.head()
        self.deploy()
        self.tool(
            f".local/share/mise/installs/bun/{sync.BUN_VERSION}/bin/bun",
            f'if [ "$1" = --version ]; then echo {sync.BUN_VERSION}; fi',
        )
        self.tool(".local/bin/agent-introspection", "exit 0")
        handler = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, handler)

    def git(self, *args: str) -> bytes:
        completed = run_bytes(
            ["git", "-C", str(self.source), *args],
            check=True,
            timeout=PROCESS_TIMEOUT,
        )
        return completed.stdout

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").decode().strip()

    def write(self, name: str, data: bytes) -> None:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def tool(self, name: str, body: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def commit(self) -> None:
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def rename(self) -> None:
        self.git(
            "mv", "agent/skills/workflow-templates", "agent/skills/omp-dyna-workflows"
        )
        self.write("agent/config.yml", b"new config")
        self.commit()

    def deployment(self, journal: Path | None = None) -> Any:
        return sync.Deployment(
            self.source, self.runtime, self.root / "backups", journal
        )

    def deploy(self, expected: dict[str, str] | None = None) -> str:
        revision: str = sync.deploy(self.deployment(), self.previous, expected)
        return revision

    def cli(self, *argv: str) -> Any:
        return patch.object(
            sync.sys,
            "argv",
            ["sync", "--source", str(self.source), "--state", str(self.state), *argv],
        )

    def home(self) -> Any:
        return patch.object(sync.Path, "home", return_value=self.root)

    def linux(self) -> Any:
        return patch.object(sync.sys, "platform", "linux")


class DeploymentTests(Fixture):
    def test_rename_removes_unchanged_retired_file_and_backs_up_privately(
        self,
    ) -> None:
        self.rename()
        self.deploy()
        assert not (self.runtime / "agent/skills/workflow-templates").exists()
        assert (
            self.runtime / "agent/skills/omp-dyna-workflows/SKILL.md"
        ).read_bytes() == b"old workflow"
        backups = list(
            (self.root / "backups").glob("*/agent/skills/workflow-templates/SKILL.md")
        )
        assert len(backups) == 1
        assert backups[0].read_bytes() == b"old workflow"
        assert backups[0].stat().st_mode & 0o777 == 0o600
        assert (self.root / "backups").stat().st_mode & 0o777 == 0o700

    def test_locally_modified_retirement_blocks_before_any_runtime_change(
        self,
    ) -> None:
        self.rename()
        retired = self.runtime / "agent/skills/workflow-templates/SKILL.md"
        retired.write_bytes(b"local changes")
        with pytest.raises(
            RuntimeError, match="Locally modified retired file preserved"
        ):
            self.deploy()
        assert retired.read_bytes() == b"local changes"
        assert (self.runtime / "agent/config.yml").read_bytes() == b"old config"
        assert not (self.runtime / "agent/skills/omp-dyna-workflows").exists()

    def test_auth_sessions_and_untracked_files_survive(self) -> None:
        private = [
            "agent/auth.json",
            "agent/sessions/history.jsonl",
            "agent/skills/local/SKILL.md",
            ".private",
        ]
        for name in private:
            path = self.runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"private fixture")
        self.rename()
        self.deploy()
        for name in private:
            assert (self.runtime / name).read_bytes() == b"private fixture"
        assert not (self.runtime / ".git").exists()

    def test_runtime_symlink_cannot_escape_into_another_directory(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        target = self.runtime / "agent/config.yml"
        target.unlink()
        target.symlink_to(outside / "config.yml")
        with pytest.raises(RuntimeError, match="Refusing runtime symlink"):
            self.deploy()
        assert not (outside / "config.yml").exists()

    def test_runtime_directory_at_tracked_path_is_refused(self) -> None:
        config = self.runtime / "agent/config.yml"
        config.unlink()
        config.mkdir()
        with pytest.raises(RuntimeError, match="Runtime destination is not a file"):
            self.deploy()
        assert config.is_dir()

    def test_symlink_in_source_is_unsupported(self) -> None:
        (self.source / "link").symlink_to("agent/config.yml")
        self.commit()
        with pytest.raises(RuntimeError, match="Unsupported tracked file: link"):
            self.deploy()
        assert not (self.runtime / "link").exists()

    def test_modified_current_file_is_preserved_before_other_changes(self) -> None:
        self.rename()
        config = self.runtime / "agent/config.yml"
        config.write_bytes(b"UI edit")
        with pytest.raises(
            RuntimeError, match="Locally modified tracked file preserved"
        ):
            self.deploy()
        assert config.read_bytes() == b"UI edit"
        assert (self.runtime / "agent/skills/workflow-templates/SKILL.md").exists()

    def test_no_change_creates_no_backups_and_preserves_expected_overlay(
        self,
    ) -> None:
        config = self.runtime / "agent/config.yml"
        config.write_bytes(b"managed overlay")
        self.deploy(sync.runtime_hashes(self.source, self.runtime))
        assert config.read_bytes() == b"managed overlay"
        assert not (self.root / "backups").exists()

    def test_missing_and_unavailable_revisions_are_explicit(self) -> None:
        state = self.root / "deployment.json"
        state.write_text(json.dumps({"revision": "0" * 40}))
        with pytest.raises(RuntimeError, match="revision is unavailable"):
            sync.load_state(self.source, state)
        state.unlink()
        assert sync.load_state(self.source, state)["revision"] == self.previous


class CommandTests(Fixture):
    def test_failed_overlay_records_hashes_and_retry_succeeds(self) -> None:
        self.rename()
        self.git("branch", "-M", "nuc-config")
        self.state.mkdir()
        state_file = self.state / "deployment.json"
        sync.save_state(state_file, self.previous, {})
        config = self.runtime / "agent/config.yml"
        failure = self.root / "overlay-fails"
        failure.touch()
        self.tool(
            ".local/bin/agent-introspection",
            f"printf 'managed overlay' > {shlex.quote(str(config))}\n"
            f"if [ -e {shlex.quote(str(failure))} ]; then exit 1; fi",
        )
        original_run = subprocess.run

        def managed_run(command: list[str], *args: Any, **kwargs: Any) -> Any:
            if command[0] == "git" and "pull" in command:
                return subprocess.CompletedProcess(command, 0)
            return original_run(command, *args, **kwargs)

        with (
            self.home(),
            self.linux(),
            self.cli("--runtime", str(self.runtime)),
            patch.object(sync.subprocess, "run", side_effect=managed_run),
        ):
            with pytest.raises(subprocess.CalledProcessError):
                sync.main()
            recorded = sync.load_state(self.source, state_file)
            assert recorded["revision"] == self.head()
            assert recorded["hashes"]["agent/config.yml"] == sync.digest(
                b"managed overlay"
            )
            failure.unlink()
            sync.main()
        assert state_file.stat().st_mode & 0o777 == 0o600
        assert config.read_bytes() == b"managed overlay"

    def test_lock_prevents_overlapping_sync_before_source_or_runtime_mutation(
        self,
    ) -> None:
        self.state.mkdir(mode=0o700)
        with (
            (self.state / "sync.lock").open("a") as held,
            self.home(),
            self.linux(),
            self.cli("--runtime", str(self.runtime)),
        ):
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(RuntimeError, match="Another configuration sync"):
                sync.main()
        assert (self.runtime / "agent/config.yml").read_bytes() == b"old config"
        assert not (self.state / "deployment.json").exists()

    def test_source_must_be_a_clean_nuc_config_checkout(self) -> None:
        with self.home(), self.linux(), self.cli():
            with pytest.raises(RuntimeError, match="Source must be on nuc-config"):
                sync.main()
            self.git("branch", "-M", "nuc-config")
            self.write("agent/config.yml", b"uncommitted")
            with pytest.raises(RuntimeError, match="Source checkout must be clean"):
                sync.main()
        assert not (self.state / "deployment.json").exists()

    def test_wrong_bun_version_is_refused(self) -> None:
        self.tool(
            f".local/share/mise/installs/bun/{sync.BUN_VERSION}/bin/bun", "echo 0.0.0"
        )
        with (
            self.home(),
            pytest.raises(RuntimeError, match=f"Install Bun {sync.BUN_VERSION}"),
        ):
            sync.dependencies()

    def test_entry_point_hides_operating_system_error_details(self) -> None:
        self.state.write_text("private fixture")
        errors = StringIO()
        with self.home(), self.linux(), self.cli(), redirect_stderr(errors):
            assert sync.run() == 1
        assert errors.getvalue() == sync.SYNC_FAILED + "\n"
        assert self.state.read_text() == "private fixture"


class RecoveryTests(Fixture):
    def interrupted_deployment(self) -> Path:
        self.rename()
        journal = self.root / "pending.json"
        original_replace = Path.replace
        interrupted = False

        def interrupt_after_write(path: Path, target: Path) -> Path:
            nonlocal interrupted
            result = original_replace(path, target)
            if self.runtime in Path(target).parents and not interrupted:
                interrupted = True
                raise OSError("injected interruption")
            return result

        with (
            patch.object(sync.Path, "replace", interrupt_after_write),
            pytest.raises(OSError, match="injected interruption"),
        ):
            sync.deploy(self.deployment(journal), self.previous)
        assert interrupted
        assert journal.stat().st_mode & 0o777 == 0o600
        return journal

    def test_partial_deployment_recovers_only_known_old_or_new_bytes(self) -> None:
        journal = self.interrupted_deployment()
        private = self.runtime / "auth.json"
        private.write_bytes(b"private fixture")
        pending = sync.load_pending(self.source, journal)
        revision = sync.deploy(
            self.deployment(journal),
            pending["previous_revision"],
            pending["hashes"],
            recovering=True,
        )
        assert revision == pending["revision"]
        assert (self.runtime / "agent/config.yml").read_bytes() == b"new config"
        assert not (self.runtime / "agent/skills/workflow-templates/SKILL.md").exists()
        assert private.read_bytes() == b"private fixture"

    def test_partial_deployment_refuses_third_hash_and_moved_head(self) -> None:
        journal = self.interrupted_deployment()
        pending = sync.load_pending(self.source, journal)
        config = self.runtime / "agent/config.yml"
        config.write_bytes(b"third unknown edit")
        with pytest.raises(
            RuntimeError, match="Locally modified tracked file preserved"
        ):
            sync.deploy(
                self.deployment(journal),
                pending["previous_revision"],
                pending["hashes"],
                recovering=True,
            )
        assert config.read_bytes() == b"third unknown edit"
        self.write("extra", b"new source")
        self.commit()
        with pytest.raises(RuntimeError, match="differs from source HEAD"):
            sync.load_pending(self.source, journal)

    def test_absent_state_persists_old_baseline_before_pull_then_completes(
        self,
    ) -> None:
        self.git("branch", "-M", "nuc-config")
        state_file = self.state / "deployment.json"
        original_run = subprocess.run

        def managed_run(command: list[str], *args: Any, **kwargs: Any) -> Any:
            if command[0] == "git" and "pull" in command:
                baseline = json.loads(state_file.read_text())
                assert baseline == {"revision": self.previous, "hashes": {}}
                self.rename()
                return subprocess.CompletedProcess(command, 0)
            return original_run(command, *args, **kwargs)

        with (
            self.home(),
            self.linux(),
            self.cli(),
            patch.object(sync.subprocess, "run", side_effect=managed_run),
        ):
            assert sync.run() == 0
        completed = sync.load_state(self.source, state_file)
        assert completed["revision"] == self.head()
        assert completed["hashes"] == sync.runtime_hashes(self.source, self.runtime)
        assert not (self.state / "pending.json").exists()

    def test_main_recovers_pending_target_before_any_pull(self) -> None:
        journal = self.interrupted_deployment()
        self.git("branch", "-M", "nuc-config")
        self.state.mkdir()
        journal.replace(self.state / "pending.json")
        original_run = subprocess.run

        def managed_run(command: list[str], *args: Any, **kwargs: Any) -> Any:
            if command[0] == "git" and "pull" in command:
                pytest.fail(
                    "Recovery must not pull before completing its pinned target"
                )
            return original_run(command, *args, **kwargs)

        with (
            self.home(),
            self.linux(),
            self.cli(),
            patch.object(sync.subprocess, "run", side_effect=managed_run),
        ):
            sync.main()
        assert not (self.state / "pending.json").exists()
        completed = sync.load_state(self.source, self.state / "deployment.json")
        assert completed["revision"] == self.head()
        assert completed["hashes"] == sync.runtime_hashes(self.source, self.runtime)
