"""guardrails CLI dispatch, its findings output and the rails-state scope filter."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import write_config

from guardrails.commands import cli
from guardrails.context.scope import Scope
from guardrails.foundation.findings import (
    Finding,
    exit_code,
    format_findings,
    remediations,
)
from guardrails.host.process import run_text

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

CONTRACT_SUBCOMMANDS = {
    "check",
    "init",
    "path-check",
    "structure",
    "rename-residue",
    "responsibility-overlap",
    "prefix-folder",
    "name-pairs",
    "consumer-candidates",
    "kind-candidates",
    "naming",
    "python-quality",
    "python-fanout",
    "python-scalability",
    "python-subprocess-timeouts",
    "toolchain-parity",
    "report-check",
    "budget-check",
    "collection-check",
    "diff-check",
    "affected",
    "mutation-check",
    "guard-run",
    "surface-lint",
    "lever-selftest",
    "adapter",
    "shadow-review",
    "config-check",
}


class FindingsTests(unittest.TestCase):
    def test_findings_render_sorted_unique_lines_then_each_rules_remediation(
        self,
    ) -> None:
        later = Finding("TEST.no-test-hooks", "b.py", "two\n words", "fix b")
        earlier = Finding("TEST.no-evasion", "a.py", "one", "fix a")
        guidance = remediations()
        assert format_findings([later, earlier, later]).splitlines() == [
            "a.py: TEST.no-evasion: one; fix a",
            "b.py: TEST.no-test-hooks: two words; fix b",
            f"remediation TEST.no-evasion: {guidance['TEST.no-evasion'].render()}",
            f"remediation TEST.no-test-hooks: {guidance['TEST.no-test-hooks'].render()}",
        ]
        assert guidance["TEST.no-evasion"].render().startswith("root cause: ")
        assert (exit_code([]), exit_code([earlier])) == (0, 1)

    def test_a_finding_whose_rule_is_not_in_the_catalogue_cannot_render(
        self,
    ) -> None:
        with pytest.raises(KeyError):
            format_findings([Finding("A.rule", "a.py", "one", "fix a")])


class RailsStateTests(unittest.TestCase):
    def test_scopes_drop_rails_state_like_git(self) -> None:
        paths = [".agentrails/config.json", ".proofs/run.md", "src/a.py", ".proofsx/b"]
        scope = Scope(paths, set(paths), paths)
        assert scope.paths == scope.inventory == ["src/a.py", ".proofsx/b"]
        assert scope.existing_paths == {"src/a.py", ".proofsx/b"}


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-cli-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        self.repo.mkdir()
        run_text(["git", "init", "-q", str(self.repo)], check=True, timeout=60)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def write_config(self, config: dict[str, Any]) -> None:
        write_config(self.repo, config)

    def located(self, *args: str) -> tuple[str, ...]:
        return (*args[:1], "--repo", str(self.repo), *args[1:])


if __name__ == "__main__":
    unittest.main()
