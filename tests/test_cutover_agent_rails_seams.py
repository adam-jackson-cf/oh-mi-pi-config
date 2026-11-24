"""scripts/cutover_agent_rails.py against real git checkouts: clone inspection and one full apply."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

from guardrails.host.process import run_text

pytestmark = pytest.mark.seam

PROCESS_TIMEOUT = 60
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/cutover_agent_rails.py"
ENGINE = ROOT / "agent/extensions/dynaflows/engine"
RECORDS = ROOT / "tests/fixtures/agentrails/records"
spec = importlib.util.spec_from_file_location("cutover_agent_rails_seams", SCRIPT)
assert spec is not None
assert spec.loader is not None
cutover = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cutover
spec.loader.exec_module(cutover)

COMMIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit")
ENTRY = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from guardrails.commands.cli import main; raise SystemExit(main())"
)


def git(*args: str) -> str:
    return run_text(["git", *args], check=True, timeout=PROCESS_TIMEOUT).stdout


class Checkouts(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.temp)

    def checkout(self, name: str, origin: str, *, head: bool = True) -> Path:
        repo = self.temp / "clones" / name
        repo.mkdir(parents=True)
        git("-C", str(repo), "init", "-q", "-b", "main")
        (repo / "readme.md").write_text("# app\n")
        git("-C", str(repo), "add", "readme.md")
        git("-C", str(repo), *COMMIT, "-q", "-m", "init")
        git(
            "-C",
            str(repo),
            "remote",
            "add",
            "origin",
            f"https://github.com/{origin}.git",
        )
        git("-C", str(repo), "update-ref", "refs/remotes/origin/trunk", "HEAD")
        if head:
            git(
                "-C",
                str(repo),
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                "refs/remotes/origin/trunk",
            )
        return repo

    def test_inspect_reads_main_checkout_origin_and_default_branch(self) -> None:
        repo = self.checkout("allowed-app", "Example-Owner/allowed-app")
        worktree = self.temp / "worktree"
        git("-C", str(repo), "worktree", "add", "-q", "-b", "feature", str(worktree))
        for clone in (repo, worktree):
            assert cutover.inspect_clone(clone) == cutover.Clone(
                repo, "example-owner/allowed-app", "trunk"
            )

    def test_inspect_refuses_a_clone_without_origin_head(self) -> None:
        repo = self.checkout("allowed-app", "example-owner/allowed-app", head=False)
        with pytest.raises(cutover.CutoverError, match="remote set-head origin --auto"):
            cutover.inspect_clone(repo)

    def test_inspect_refuses_a_directory_outside_git(self) -> None:
        with pytest.raises(cutover.CutoverError, match="is not in a git repository"):
            cutover.inspect_clone(self.temp)

    def run_cutover(self, *args: str) -> tuple[int, str, str]:
        result = run_text(
            [sys.executable, str(SCRIPT), "records", *args], timeout=PROCESS_TIMEOUT
        )
        return result.returncode, result.stdout, result.stderr

    def test_apply_writes_an_excluded_config_that_the_repo_check_accepts(self) -> None:
        root = self.temp / "repos"
        shutil.copytree(RECORDS, root)
        repo = self.checkout("plain-lib", "example-owner/plain-lib")
        code, out, err = self.run_cutover(
            "--records-root",
            str(root),
            "--repo",
            f"example-owner/plain-lib={repo}",
            "--approved-by",
            "example-owner",
            "--approved-at",
            "2026-11-24T09:00:00Z",
            "--archive",
            str(self.temp / "archive"),
            "--apply",
        )
        assert code == 0, err
        assert "example-owner/plain-lib: wrote" in out
        config = json.loads((repo / ".agentrails/config.json").read_text())
        assert config["repo"] == {
            "slug": "example-owner/plain-lib",
            "defaultBranch": "trunk",
        }
        assert git("-C", str(repo), "status", "--short", "--ignored=no") == ""
        check = run_text(
            [
                sys.executable,
                "-c",
                ENTRY,
                str(ENGINE),
                "config-check",
                "--repo",
                str(repo),
            ],
            timeout=PROCESS_TIMEOUT,
        )
        assert check.returncode == 0, check.stdout
        assert json.loads(check.stdout)["problems"] == []

    def test_origin_mismatch_refuses_and_writes_nothing(self) -> None:
        root = self.temp / "repos"
        shutil.copytree(RECORDS, root)
        repo = self.checkout("plain-lib", "someone-else/plain-lib")
        code, _, err = self.run_cutover(
            "--records-root",
            str(root),
            "--repo",
            f"example-owner/plain-lib={repo}",
            "--approved-by",
            "example-owner",
            "--approved-at",
            "2026-11-24T09:00:00Z",
            "--archive",
            str(self.temp / "archive"),
            "--apply",
        )
        assert code == 1
        assert "origin remote names 'someone-else/plain-lib'" in err
        assert not (repo / ".agentrails").exists()
        assert not (self.temp / "archive").exists()
