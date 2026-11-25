from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.context.config import repo_config
from guardrails.context.config.repo_config import ConfigError
from guardrails.host.process import run_text

pytestmark = pytest.mark.seam

PROCESS_TIMEOUT = 60
ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "agent/extensions/agent-rails/engine"
MINIMAL = ROOT / "tests/fixtures/agentrails/valid/minimal.json"
ENTRY = (
    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
    "from guardrails.commands.cli import main; raise SystemExit(main())"
)
COMMIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit")


def git(*args: str) -> str:
    return run_text(["git", *args], check=True, timeout=PROCESS_TIMEOUT).stdout


def config_check(
    *args: str, env: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    result = run_text(
        [sys.executable, "-c", ENTRY, str(ENGINE), "config-check", *args],
        env=env,
        timeout=PROCESS_TIMEOUT,
    )
    assert result.returncode in {0, 1}, result.stderr
    return result.returncode, json.loads(result.stdout)


class Repositories(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(os.path.realpath(temp.name))
        self.main = self.temp / "ledger"
        git("init", "-q", "-b", "main", str(self.main))
        git("-C", str(self.main), *COMMIT, "-q", "--allow-empty", "-m", "init")
        self.worktree = self.temp / "ledger-feature"
        git(
            "-C",
            str(self.main),
            "worktree",
            "add",
            "-q",
            "-b",
            "feature",
            str(self.worktree),
        )

    def add_origin(self, url: str) -> None:
        git("-C", str(self.main), "remote", "add", "origin", url)

    def write_config(self, config: dict[str, Any]) -> Path:
        path = self.main / ".agentrails/config.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(config))
        return path

    def test_a_linked_worktree_resolves_to_the_main_checkout(self) -> None:
        assert repo_config.main_checkout(self.worktree) == self.main
        assert repo_config.main_checkout(self.main / ".git") == self.main
        assert repo_config.config_path(self.worktree) == (
            self.main / ".agentrails/config.json"
        )

    def test_the_origin_slug_is_lowercase_and_absent_without_a_github_origin(
        self,
    ) -> None:
        assert repo_config.origin_slug(self.worktree) is None
        self.add_origin("https://gitlab.example/owner/ledger.git")
        assert repo_config.origin_slug(self.worktree) is None
        git(
            "-C",
            str(self.main),
            "remote",
            "set-url",
            "origin",
            "git@github.com:Example-Owner/Ledger.git",
        )
        assert repo_config.origin_slug(self.worktree) == "example-owner/ledger"

    def test_a_bare_repository_has_no_main_checkout(self) -> None:
        bare = self.temp / "ledger.git"
        git("clone", "-q", "--bare", str(self.main), str(bare))
        linked = self.temp / "from-bare"
        git("-C", str(bare), "worktree", "add", "-q", str(linked), "main")
        for where in (bare, linked):
            with self.subTest(where=where.name), pytest.raises(ConfigError) as caught:
                repo_config.main_checkout(where)
            assert caught.value.kind == "unresolved"
            assert "no main checkout" in caught.value.problems[0]

    def test_a_directory_outside_git_is_outside_and_a_missing_one_unresolved(
        self,
    ) -> None:
        plain = self.temp / "plain"
        plain.mkdir()
        with pytest.raises(ConfigError) as caught:
            repo_config.main_checkout(plain)
        assert caught.value.kind == "outside"
        assert repo_config.origin_slug(plain) is None
        with pytest.raises(ConfigError) as caught:
            repo_config.main_checkout(self.temp / "absent")
        assert caught.value.kind == "unresolved"

    def test_an_unreadable_git_config_leaves_the_origin_unresolved(self) -> None:
        (self.main / ".git/config").write_text('[remote "origin"\n')
        with pytest.raises(ConfigError) as caught:
            repo_config.origin_slug(self.main)
        assert caught.value.kind == "unresolved"
        assert "git remote get-url origin" in caught.value.problems[0]

    def test_load_config_reads_and_validates_the_main_checkout_s_file(self) -> None:
        self.add_origin("https://github.com/example-owner/ledger.git")
        with pytest.raises(ConfigError) as caught:
            repo_config.load_config(self.worktree)
        assert caught.value.kind == "missing"
        config = json.loads(MINIMAL.read_text())
        self.write_config(config)
        assert repo_config.load_config(self.worktree) == config
        config["repo"]["slug"] = "example-owner/other"
        self.write_config(config)
        with pytest.raises(ConfigError) as caught:
            repo_config.load_config(self.worktree)
        assert caught.value.kind == "invalid"
        assert caught.value.problems[0].startswith(
            "repo.slug: is 'example-owner/other'"
        )

    def test_repo_form_prints_one_json_line_per_outcome(self) -> None:
        path = self.main / ".agentrails/config.json"
        assert config_check("--repo", str(self.worktree)) == (
            1,
            {
                "ok": False,
                "kind": "outside",
                "path": str(path),
                "problems": [
                    f"{self.worktree} has no GitHub origin remote; agent rails "
                    "configure GitHub repositories only"
                ],
            },
        )
        self.add_origin("https://github.com/example-owner/ledger.git")
        code, result = config_check("--repo", str(self.worktree))
        assert (code, result["kind"], result["path"]) == (1, "missing", str(path))
        self.write_config(json.loads(MINIMAL.read_text()))
        assert config_check("--repo", str(self.worktree)) == (
            0,
            {"ok": True, "kind": "ok", "path": str(path), "problems": []},
        )
        path.write_text("{")
        code, result = config_check("--repo", str(self.worktree))
        assert (code, result["kind"]) == (1, "invalid")

    def test_repo_form_is_unresolved_when_git_cannot_run(self) -> None:
        code, result = config_check("--repo", str(self.worktree), env={"PATH": ""})
        assert (code, result["kind"], result["path"]) == (1, "unresolved", None)
        assert "could not run" in result["problems"][0]

    def test_help_describes_the_three_forms(self) -> None:
        result = run_text(
            [sys.executable, "-c", ENTRY, str(ENGINE), "config-check", "--help"],
            timeout=PROCESS_TIMEOUT,
        )
        assert result.returncode == 0, result.stderr
        for word in (
            "guardrails config-check",
            "--repo",
            "--file",
            "--section",
            "--slug",
        ):
            assert word in result.stdout


if __name__ == "__main__":
    unittest.main()
