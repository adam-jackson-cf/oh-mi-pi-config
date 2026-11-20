from __future__ import annotations

import shutil
import sys
import unittest

import pytest
from test_reference_init import FAKE_SERVER, InitCase

from guardrails.commands.init import lsp
from guardrails.context.config.profile import load_profile

pytestmark = pytest.mark.seam


class LspTests(InitCase):
    def test_missing_server_is_a_setup_gap_unless_allowed(self) -> None:
        only_git = self.repo.parent / "git-bin"
        only_git.mkdir()
        (only_git / "git").symlink_to(shutil.which("git") or "git")
        self.use_path(only_git)
        code, output = self.init()
        assert code == 1
        assert "PROFILE.complete" in output
        assert "pyright-langserver is not on PATH" in output
        code, output = self.init("--allow-missing-lsp")
        assert code == 0, output
        assert "allowed by --allow-missing-lsp" in output

    def test_detects_typescript_markers(self) -> None:
        (self.repo / "pyproject.toml").unlink()
        self.write("tsconfig.json", "{}")
        stacks = lsp.detect(self.repo)
        assert [stack.server for stack in stacks] == ["typescript-language-server"]

    def test_typescript_six_keeps_the_javascript_language_server(self) -> None:
        self.write("package.json", "{}")
        self.write("node_modules/typescript/package.json", '{"version": "6.0.3"}')
        stack = lsp.detect(self.repo)[1]
        assert stack.server == "typescript-language-server"
        assert stack.arguments == ("--stdio",)

    def test_typescript_seven_queries_the_installed_native_server(self) -> None:
        self.write("package.json", "{}")
        self.write("node_modules/typescript/package.json", '{"version": "7.0.2"}')
        native = self.repo / "node_modules/typescript/bin/tsc"
        self.write(
            "node_modules/typescript/bin/tsc",
            f"#!{sys.executable}\nassert __import__('sys').argv[1:] == ['--lsp', '--stdio']\n"
            + FAKE_SERVER,
        )
        native.chmod(0o755)
        stack = lsp.detect(self.repo)[1]
        assert stack.server == str(native)
        findings, lines = lsp.readiness(self.repo, [stack], allow_missing=False)
        assert findings == []
        assert "canary ok: references in a canary module" in lines[0]

    def test_repo_without_stack_records_no_server(self) -> None:
        (self.repo / "pyproject.toml").unlink()
        code, output = self.init()
        assert code == 0, output
        assert "lsp" not in load_profile(self.record)["command_notes"]
        assert "lsp: no pyproject.toml" in output


class CanaryTests(InitCase):
    def test_canary_queries_a_symbol_from_the_repo(self) -> None:
        self.write(
            "src/target/core.py", "import os\n\n\ndef total(values):\n    return 1\n"
        )
        code, output = self.init()
        assert code == 0, output
        assert (
            "canary ok: references src/target/core.py:4 total -> 1 locations" in output
        )

    def test_canary_uses_a_canary_module_when_the_repo_has_no_source(self) -> None:
        code, output = self.init()
        assert code == 0, output
        assert "canary ok: references in a canary module omp_lsp_canary" in output
        assert not list(self.repo.glob("omp_lsp_*"))

    def test_server_that_exits_fails_even_when_missing_servers_are_allowed(
        self,
    ) -> None:
        self.executable("pyright-langserver", "import sys\nsys.exit(97)\n")
        code, output = self.init("--allow-missing-lsp")
        assert code == 1
        assert "PROFILE.complete" in output
        assert "failed the canary: the server exited (code 97)" in output

    def test_server_that_never_answers_fails_within_the_time_limit(self) -> None:
        self.executable("pyright-langserver", "import time\ntime.sleep(30)\n")
        code, output = self.init("--lsp-timeout", "0.5")
        assert code == 1
        assert "no answer to initialize before the time limit" in output


if __name__ == "__main__":
    unittest.main()
