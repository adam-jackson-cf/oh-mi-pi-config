from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

from guardrails.host.process import run_attached, run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import (
    config,
    exception as agentrails_exception,
    write_config,
)

from guardrails.commands import cli
from guardrails.commands.check import whole_repo
from guardrails.commands.python_policy import (
    fanout,
    quality,
    scalability,
    subprocess_timeouts,
)
from guardrails.foundation.findings import Finding

pytestmark = pytest.mark.seam


ROOT = Path(__file__).resolve().parents[1]


FIXTURE = json.loads((ROOT / "tests/fixtures/python_policy/shop-repo.json").read_text())


CHECKERS: dict[str, Callable[[Path, dict[str, Any], list[str]], list[Finding]]] = {
    "quality": quality.run,
    "scalability": scalability.run,
    "fanout": fanout.run,
}


def merged_settings(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


class ShopRepo:
    def __init__(
        self,
        test: unittest.TestCase,
        files: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        directory = tempfile.TemporaryDirectory()
        test.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        run_attached(["git", "init", "-q", str(self.path)], check=True, timeout=60)
        for relpath, text in {**FIXTURE["files"], **(files or {})}.items():
            target = self.path / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.repo_config = copy.deepcopy(FIXTURE["config"])
        python = self.repo_config["checkers"]["python"]
        self.repo_config["checkers"]["python"] = merged_settings(python, settings or {})

    def run(self, checker: str, *argv: str) -> list[Finding]:
        return CHECKERS[checker](self.path, self.repo_config, list(argv))


def rules_at(findings: list[Any]) -> set[tuple[str, str]]:
    return {(item.rule_id, item.path) for item in findings}


class ConfiguredCleanRepoTests(unittest.TestCase):
    def test_configured_repository_passes_every_python_policy_checker(self) -> None:
        repo = ShopRepo(self)
        for checker in CHECKERS:
            with self.subTest(checker=checker):
                assert repo.run(checker) == []

    def test_each_planted_violation_is_reported_at_its_path(self) -> None:
        for case in FIXTURE["violations"]:
            label = f"{case['rule']} {case['path']} {case['note']}".strip()
            with self.subTest(label):
                repo = ShopRepo(self, case["files"], case["settings"])
                findings = repo.run(case["checker"])
                assert (case["rule"], case["path"]) in rules_at(findings), findings
                for item in findings:
                    assert item.fix, item


class PythonNameScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = ShopRepo(
            self,
            {
                "lib/orphan.py": "VALUE = 1\n",
                "scripts/BadCommand.py": "VALUE = 1\n",
                "tests/nested/check.py": "VALUE = 1\n",
                "src/shop/common/BadFolder/misc.py": "VALUE = 1\n",
                "src/shop/BadModule.py": "VALUE = 1\n",
                ".agents/skills/BadSkill/SKILL.md": "# Skill\n",
            },
        )
        self.environment = dict(
            os.environ,
            HOME=str(self.repo.path),
            XDG_CONFIG_HOME=str(self.repo.path / ".config"),
            XDG_CACHE_HOME=str(self.repo.path / ".cache"),
            XDG_DATA_HOME=str(self.repo.path / ".local/share"),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=os.devnull,
        )
        self.repo.repo_config["checkers"]["check"] = {"run": ["python-quality"]}
        self.name_rules = {f"PYQ{number}" for number in range(100, 108)}
        self.commit()

    def git(self, *args: str) -> str:
        return run_text(
            ["git", "-C", str(self.repo.path), *args],
            env=self.environment,
            timeout=30,
            check=True,
        ).stdout

    def commit(self) -> None:
        self.git("add", "-A")
        self.git(
            "-c",
            "user.name=Python scope outcomes",
            "-c",
            "user.email=python-scope@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )

    def write(self, path: str, source: str = "VALUE = 1\n") -> None:
        target = self.repo.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source)

    def check(self) -> list[Any]:
        return whole_repo.run(self.repo.path, self.repo.repo_config, [])

    def test_automatic_commit_scope_preserves_legacy_names_but_explicit_scan_reports_them(
        self,
    ) -> None:
        assert self.check() == []
        for argv in ([], ["--all"]):
            found = self.repo.run("quality", *argv)
            assert {item.rule_id for item in found} & self.name_rules == self.name_rules

    def test_unchanged_legacy_content_quality_still_blocks_automatic_commit_check(
        self,
    ) -> None:
        self.write(
            "src/shop/contracts.py",
            "from collections.abc import Callable\nfrom typing import Any\n\n"
            "def price(pricer: Callable[[int], int], raw: Any) -> int:\n"
            "    return pricer(raw)\n",
        )
        self.commit()
        self.write("src/shop/cli.py", "VALUE = 2\n")
        self.git("add", "src/shop/cli.py")
        found = self.check()
        assert ("PYQ230", "src/shop/contracts.py") in rules_at(found)
        assert not {item.rule_id for item in found} & self.name_rules

    def test_unstaged_and_untracked_content_quality_blocks_with_new_clean_staged_path(
        self,
    ) -> None:
        source = (
            "from collections.abc import Callable\nfrom typing import Any\n\n"
            "def price(pricer: Callable[[int], int], raw: Any) -> int:\n"
            "    return pricer(raw)\n"
        )
        self.write("src/shop/contracts.py", source)
        self.write("src/shop/receipt.py", source)
        self.repo.repo_config["checkers"]["python"]["contract_files"].append(
            "src/shop/receipt.py"
        )
        self.write("src/shop/reader.py")
        self.git("add", "src/shop/reader.py")
        found = self.check()
        assert ("PYQ230", "src/shop/contracts.py") in rules_at(found)
        assert ("PYQ230", "src/shop/receipt.py") in rules_at(found)
        assert not {item.rule_id for item in found} & self.name_rules

    def test_new_files_judge_only_new_segments_under_legacy_parent_directories(
        self,
    ) -> None:
        self.write("src/shop/common/BadFolder/reader.py")
        self.write(".agents/skills/BadSkill/reader.py")
        self.git("add", "-A")
        assert self.check() == []
        self.write("src/shop/common/BadFolder/helpers/reader.py")
        self.write("src/shop/common/BadFolder/BadChild/reader.py")
        self.write(".agents/skills/NewBadSkill/SKILL.md", "# Skill\n")
        self.git("add", "-A")
        found = self.check()
        assert ("PYQ103", "src/shop/common/BadFolder/helpers/reader.py") in rules_at(
            found
        )
        assert ("PYQ105", "src/shop/common/BadFolder/BadChild/reader.py") in rules_at(
            found
        )
        assert ("PYQ106", ".agents/skills/NewBadSkill") in rules_at(found)
        assert not any(
            item.rule_id in self.name_rules
            and (
                "'common'" in item.message
                or "'BadFolder'" in item.message
                or "'BadSkill'" in item.message
            )
            for item in found
        )

    def test_new_and_renamed_destinations_receive_file_naming_and_placement_checks(
        self,
    ) -> None:
        self.git("mv", "src/shop/orders.py", "src/shop/utils.py")
        paths = {
            "src/shop/AnotherBad.py": "PYQ107",
            "lib/reader.py": "PYQ100",
            "scripts/BadNew.py": "PYQ101",
            "tests/nested/reader.py": "PYQ102",
        }
        for path in paths:
            self.write(path)
        self.git("add", "-A")
        found = rules_at(self.check())
        assert ("PYQ104", "src/shop/utils.py") in found
        for path, rule in paths.items():
            assert (rule, path) in found
        assert ("PYQ107", "src/shop/BadModule.py") not in found

    def test_explicit_edit_paths_ignore_legacy_names_and_check_new_destinations(
        self,
    ) -> None:
        self.write("src/shop/BadModule.py", "VALUE = 2\n")
        assert self.repo.run("quality", "src/shop/BadModule.py") == []
        self.write("src/shop/common/BadFolder/reader.py")
        assert self.repo.run("quality", "src/shop/common/BadFolder/reader.py") == []
        self.write("src/shop/common/BadFolder/helpers/reader.py")
        found = self.repo.run("quality", "src/shop/common/BadFolder/helpers/reader.py")
        assert ("PYQ103", "src/shop/common/BadFolder/helpers/reader.py") in rules_at(
            found
        )


class FailClosedDefaultTests(unittest.TestCase):
    def test_without_settings_every_file_is_unmapped_and_fanout_asks_for_roots(
        self,
    ) -> None:
        repo = ShopRepo(self)
        repo.repo_config = {"tests": {"public_entry_points": {"shop": "shop.cli:main"}}}
        unmapped = {
            item.path for item in repo.run("quality") if item.rule_id == "PYQ100"
        }
        expected = {path for path in FIXTURE["files"] if path.endswith(".py")}
        assert expected == unmapped
        assert sorted(rules_at(repo.run("fanout"))) == [
            ("FANOUT.general", ".agentrails/config.json#checkers")
        ]

    def test_empty_script_allowlist_rejects_every_command(self) -> None:
        repo = ShopRepo(self, settings={"script_names": []})
        assert ("PYQ101", "scripts/build-catalogue.py") in rules_at(repo.run("quality"))

    def test_process_scope_defaults_to_every_maintained_root(self) -> None:
        repo = ShopRepo(self)
        del repo.repo_config["checkers"]["python"]["subprocess_wrappers"]
        found = rules_at(repo.run("quality"))
        assert ("PYQ220", "src/shop/process.py") in found
        assert ("PYQ220", "scripts/build-catalogue.py") in found


class SettingsTests(unittest.TestCase):
    def test_banned_words_come_from_the_shipped_house_style(self) -> None:
        repo = ShopRepo(self, {"src/shop/legacy/pricing.py": "X = 1\n"})
        assert ("PYQ103", "src/shop/legacy/pricing.py") in rules_at(repo.run("quality"))
        approved = ShopRepo(
            self,
            {"src/shop/legacy/pricing.py": "X = 1\n"},
            {"generic_name_allowlist": ["src/shop/legacy"]},
        )
        assert approved.run("quality") == []

    def test_guardrail_exception_with_reason_drops_the_finding(self) -> None:
        repo = ShopRepo(self, {"src/shop/utils.py": "X = 1\n"})
        repo.repo_config["exceptions"] = [
            agentrails_exception(
                "PYQ104",
                "src/shop/utils.py",
                "Owner-approved upstream compatibility filename",
            )
        ]
        assert repo.run("quality") == []

    def test_glob_roots_and_data_label_directories(self) -> None:
        files = {"lab/stages/EU-West/pricing/tool/src/run_stage.py": "X = 1\n"}
        settings = {
            "maintained_roots": [
                "src/shop",
                "scripts",
                "tests",
                ".agents/skills",
                "lab/stages/*/*/tool/src",
            ]
        }
        assert ShopRepo(self, files, settings).run("quality") == []
        files = {"src/shop/regions/EU-West/rates.py": "X = 1\n"}
        flagged = ShopRepo(self, files).run("quality")
        assert ("PYQ105", "src/shop/regions/EU-West/rates.py") in rules_at(flagged)
        labelled = ShopRepo(self, files, {"data_label_dirs": ["src/shop/regions/*"]})
        assert labelled.run("quality") == []

    def test_excluded_paths_and_tool_caches_are_not_maintained_source(self) -> None:
        files = {"vendor/upstream.py": "X = 1\n", ".venv/lib/site.py": "X = 1\n"}
        repo = ShopRepo(self, files, {"excluded_paths": ["vendor"]})
        assert repo.run("quality") == []

    def test_path_arguments_limit_the_scan(self) -> None:
        repo = ShopRepo(
            self, {"lib/orphan.py": "X = 1\n", "src/shop/utils.py": "X = 1\n"}
        )
        assert {("PYQ104", "src/shop/utils.py")} == rules_at(
            repo.run("quality", "src/shop")
        )

    def test_contract_json_record_binding_must_match_exactly(self) -> None:
        source = "from typing import Any\n\n\ndef price(pricer: int, raw: dict[str, Any]) -> int:\n    return 1\n"
        repo = ShopRepo(self, {"src/shop/contracts.py": source})
        assert ("PYQ231", "src/shop/contracts.py") in rules_at(repo.run("quality"))

    def test_loop_depth_limit_and_scope_are_configurable(self) -> None:
        nested = {
            "src/shop/pairs.py": "def pairs(rows):\n    for a in rows:\n        for b in a:\n            yield b\n"
        }
        assert ShopRepo(self, nested).run("scalability") == []
        strict = ShopRepo(self, nested, {"max_loop_depth": 1})
        assert ("PYS250", "src/shop/pairs.py") in rules_at(strict.run("scalability"))
        unscoped = ShopRepo(
            self, nested, {"max_loop_depth": 1, "loop_roots": ["scripts"]}
        )
        assert unscoped.run("scalability") == []

    def test_function_bodies_reset_loop_depth(self) -> None:
        source = (
            "def outer(rows):\n    for row in rows:\n        for cell in row:\n"
            "            def inner(items):\n                for item in items:\n"
            "                    yield item\n            yield from inner(cell)\n"
        )
        assert ShopRepo(self, {"src/shop/walk.py": source}).run("scalability") == []

    def test_fanout_counts_first_party_modules_and_ignores_stdlib(self) -> None:
        source = "import os\nimport json\nfrom shop import orders, process\nfrom . import cli\n"
        repo = ShopRepo(
            self, {"src/shop/hub.py": source}, {"fanout": {"architectural_max": 2}}
        )
        findings = repo.run("fanout")
        assert {("FANOUT.general", "src/shop/hub.py")} == rules_at(findings)
        assert "shop.cli, shop.orders, shop.process" in findings[0].message

    def test_fanout_root_packages_default_to_public_entry_point_packages(self) -> None:
        source = "import shop.orders\nimport shop.process\n"
        settings = {"fanout": {"architectural_max": 1}}
        repo = ShopRepo(self, {"src/shop/hub.py": source}, settings)
        repo.repo_config["tests"]["public_entry_points"] = {}
        assert "fanout.root_packages" in repo.run("fanout")[0].message


class CliTests(unittest.TestCase):
    def write_repo_config(self, repo: ShopRepo) -> None:
        write_config(repo.path, config(**repo.repo_config))

    def test_cli_dispatches_each_python_subcommand(self) -> None:
        repo = ShopRepo(self, {"src/shop/utils.py": "X = 1\n"})
        self.write_repo_config(repo)
        expected = {"python-quality": 1, "python-scalability": 0, "python-fanout": 0}
        for name, code in expected.items():
            with self.subTest(name), contextlib.redirect_stdout(io.StringIO()) as out:
                argv = [name, "--repo", str(repo.path)]
                assert code == cli.main(argv)
            if code:
                assert "src/shop/utils.py: PYQ104:" in out.getvalue()


TIMEOUTS_REPO_CONFIG = {"tests": {"layout": {"test_file_patterns": ["test_*.py"]}}}


class SubprocessTimeoutTests(unittest.TestCase):
    def repo(self, files: dict[str, str]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        run_attached(["git", "init", "-q", str(path)], check=True, timeout=60)
        for relpath, text in files.items():
            target = path / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return path

    def check(
        self, files: dict[str, str], repo_config: dict[str, Any] = TIMEOUTS_REPO_CONFIG
    ) -> list[Any]:
        return subprocess_timeouts.run(self.repo(files), repo_config, [])

    def test_bounded_process_calls_pass(self) -> None:
        bounded = (
            "import asyncio\nimport subprocess as sp\nfrom subprocess import Popen\n\n\n"
            "def test_sync():\n    sp.run(['true'], timeout=5, check=True)\n\n\n"
            "def test_popen():\n    with Popen(['cat']) as proc:\n        proc.communicate(b'', 5)\n\n\n"
            "async def test_async():\n"
            "    proc = await asyncio.create_subprocess_exec('true')\n"
            "    await asyncio.wait_for(proc.wait(), timeout=5)\n\n\n"
            "def test_unrelated(client):\n    client.run()\n    client.wait()\n"
        )
        assert self.check({"tests/unit/test_proc.py": bounded}) == []

    def test_unbounded_process_calls_are_reported_by_line(self) -> None:
        unbounded = (
            "import asyncio\nimport subprocess\n\n\n"
            "def test_sync():\n    subprocess.check_output(['true'])\n\n\n"
            "def test_zero():\n    subprocess.run(['true'], timeout=0)\n\n\n"
            "def test_popen():\n    proc = subprocess.Popen(['cat'])\n    proc.wait()\n\n\n"
            "async def test_async():\n"
            "    proc = await asyncio.create_subprocess_exec('true')\n    await proc.wait()\n"
        )
        findings = self.check({"tests/boundary/test_proc.py": unbounded})
        assert sorted(item.message.split(":", 1)[0] for item in findings) == [
            "line 10",
            "line 15",
            "line 20",
            "line 6",
        ]
        assert all(item.rule_id == "TEST.subprocess-timeouts" for item in findings)
        assert "positive timeout" in findings[0].fix

    def test_only_test_files_are_checked_and_conftest_counts(self) -> None:
        call = "import subprocess\nsubprocess.run(['true'])\n"
        findings = self.check({
            "src/shop/process.py": call,
            "tests/conftest.py": call,
            "tests/unit/helper.py": call,
        })
        assert rules_at(findings) == {("TEST.subprocess-timeouts", "tests/conftest.py")}

    def test_unparsable_test_file_is_reported(self) -> None:
        findings = self.check({"tests/unit/test_bad.py": "def test_x(:\n"})
        assert rules_at(findings) == {
            ("TEST.subprocess-timeouts", "tests/unit/test_bad.py")
        }

    def test_cli_runs_python_test_hygiene(self) -> None:
        path = self.repo({
            "tests/unit/test_proc.py": "import subprocess\nsubprocess.call(['x'])\n"
        })
        write_config(path, config(**TIMEOUTS_REPO_CONFIG))
        argv = ["python-subprocess-timeouts", "--repo", str(path)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            assert cli.main(argv) == 1
        assert (
            "tests/unit/test_proc.py: TEST.subprocess-timeouts: line 2:"
            in out.getvalue()
        )


class StandaloneExclusionTests(unittest.TestCase):
    def exit_codes(
        self, step: str, repo: Path, repo_config: dict[str, Any]
    ) -> list[int]:
        codes = []
        for exclude in ([], ["tests/**"]):
            excluded = {**repo_config, "checkers": {**repo_config.get("checkers", {})}}
            excluded["checkers"]["check"] = {"exclude": exclude}
            write_config(repo, config(**excluded))
            argv = [step, "--repo", str(repo)]
            with contextlib.redirect_stdout(io.StringIO()):
                codes.append(cli.main(argv))
        return codes

    def test_python_quality_drops_findings_under_the_record_exclusions(self) -> None:
        repo = ShopRepo(self, {"tests/BadName/check.py": "VALUE = 1\n"})
        assert repo.run("quality")
        assert self.exit_codes("python-quality", repo.path, repo.repo_config) == [1, 0]

    def test_python_test_hygiene_drops_findings_under_the_record_exclusions(
        self,
    ) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        run_attached(["git", "init", "-q", str(path)], check=True, timeout=60)
        (path / "tests/unit").mkdir(parents=True)
        (path / "tests/unit/test_proc.py").write_text(
            "import subprocess\n\n\ndef test_sync():\n    subprocess.check_output(['true'])\n"
        )
        assert self.exit_codes(
            "python-subprocess-timeouts", path, TIMEOUTS_REPO_CONFIG
        ) == [
            1,
            0,
        ]


def test_engine_modules_stay_within_the_default_fanout_limit() -> None:
    repo_config = {
        "checkers": {
            "python": {
                "source_roots": ["agent/extensions/agent-rails/engine"],
                "fanout": {"root_packages": ["checkers"]},
            }
        }
    }
    findings = fanout.run(ROOT, repo_config, ["agent/extensions/agent-rails/engine"])
    assert [(item.path, item.message) for item in findings] == []


if __name__ == "__main__":
    unittest.main()
