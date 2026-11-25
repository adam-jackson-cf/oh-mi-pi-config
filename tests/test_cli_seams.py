from __future__ import annotations

import copy
import sys
import unittest
import unittest.mock
from pathlib import Path

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)


import pytest
from test_agentrails_fixture import fixture
from test_cli import CONTRACT_SUBCOMMANDS, ROOT, CliCase

from guardrails.context.config import adapter_declarations, validator
from guardrails.subcommand import dispatch

pytestmark = pytest.mark.seam


ENGINE = ROOT / "agent/extensions/agent-rails/engine"


class CliTests(CliCase):
    def test_subcommand_help_needs_no_config_or_pytest_extra(self) -> None:
        entry = (
            "import sys; sys.modules['pytest'] = None; sys.modules['ast_grep_py'] = None; "
            "from guardrails.commands import cli; raise SystemExit(cli.main(sys.argv[1:]))"
        )
        # shadow-review reads the decision store, not a repository: it takes no --repo.
        for name in sorted(CONTRACT_SUBCOMMANDS - {"adapter", "shadow-review"}):
            with self.subTest(subcommand=name):
                result = run_text(
                    [sys.executable, "-c", entry, *self.located(name, "--help")],
                    cwd=ENGINE,
                    timeout=60,
                )
                assert result.returncode == 0, result.stderr
                assert name in result.stdout
                assert "does not exist" not in result.stdout
        result = run_text(
            [sys.executable, "-c", entry, "shadow-review", "--help"],
            cwd=ENGINE,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        for word in (
            "shadow-review",
            "export",
            "import",
            "report",
            "inspector",
            "--machine",
        ):
            assert word in result.stdout

    def test_checker_modules_are_imported_only_when_their_subcommand_runs(self) -> None:
        probe = (
            "import sys; from guardrails.commands import cli; "
            "listed = {m for item in cli.SUBCOMMANDS.values() for m in item.modules}; "
            "print(sorted(listed & set(sys.modules) - {'guardrails.context.config.section_schema'}))"
        )
        result = run_text(
            [sys.executable, "-c", probe], cwd=ENGINE, check=True, timeout=60
        )
        assert result.stdout.strip() == "[]"


class ConfigCliTests(CliCase):
    def test_every_contract_subcommand_is_listed(self) -> None:
        assert set(dispatch.SUBCOMMANDS) == CONTRACT_SUBCOMMANDS
        code, out, _ = self.run_cli("--help")
        assert code == 0
        for name in CONTRACT_SUBCOMMANDS:
            assert name in out

    def test_subcommand_help_lists_each_checker_module_s_catalogue_rules(self) -> None:
        code, out, _ = self.run_cli("diff-check", "--help")
        assert code == 0
        assert (
            "guardrails.commands.check.integrity:\n"
            "  CODE.no-comments: Block added comments of every supported syntax, "
            "including Python docstrings; preserve tool directives.\n"
        ) in out
        assert (
            "  TEST.duplicate-path: Reject new tests duplicating an existing call path "
            "and assertion."
        ) in out

    def test_a_missing_or_invalid_config_refuses_every_checker(self) -> None:
        code, out, err = self.run_cli(*self.located("naming"))
        assert (code, out) == (2, "")
        assert err.startswith("config outside: ") or err.startswith("config missing: ")
        broken = fixture()
        broken["tests"]["adapters"][0]["name"] = "TBD"
        self.write_config(broken)
        code, out, err = self.run_cli(*self.located("naming"))
        assert (code, out) == (2, "")
        assert err.startswith("config invalid: tests.adapters[0].name: ")

    def test_gates_rules_switch_off_single_code_check_rules(self) -> None:
        config = fixture()
        self.write_config(config)
        (self.repo / "src/ledger").mkdir(parents=True)
        (self.repo / "src/ledger/TBD_misc.py").write_text("x = 1\n")
        code, out, _ = self.run_cli(*self.located("naming"), "--all")
        assert code == 1
        reported = {
            line.split(": ")[1]
            for line in out.splitlines()
            if not line.startswith("remediation ") and line.count(": ") >= 2
        }
        assert reported <= validator.code_check_rules(), reported
        off = copy.deepcopy(config)
        off["gates"]["rules"] = dict.fromkeys(sorted(reported), False)
        self.write_config(off)
        assert self.run_cli(*self.located("naming"), "--all")[:2] == (
            0,
            "naming: ok\n",
        )

    def test_unknown_subcommand_and_missing_adapter_are_usage_errors(self) -> None:
        assert self.run_cli("lint-everything")[0] == 2
        config = fixture()
        self.write_config(config)
        with unittest.mock.patch.object(
            adapter_declarations,
            "names",
            return_value=["framework_without_adapter"],
        ):
            code, _, err = self.run_cli(
                *self.located("adapter"), "framework_without_adapter", "lint"
            )
        assert code == 2
        assert "cannot import guardrails.commands.adapters" in err

    def test_adapter_takes_a_declared_adapter_name_first(self) -> None:
        self.write_config(fixture())
        for argv in ([], ["vitest", "lint"], ["lint"]):
            with self.subTest(argv=argv):
                code, out, _ = self.run_cli(*self.located("adapter"), *argv)
                assert code == 1
                assert (
                    "adapter takes one of the config's tests.adapters ['pytest']" in out
                )


if __name__ == "__main__":
    unittest.main()
