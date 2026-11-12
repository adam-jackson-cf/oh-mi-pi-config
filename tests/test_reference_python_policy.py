"""PYQ, PYS and FANOUT checkers of the guardrails reference on small configured repositories."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/skills/setup-repository-guardrails/assets/python-reference"
FIXTURE = json.loads((ROOT / "tests/fixtures/reference/python_policy/shop-repo.json").read_text())
sys.path.insert(0, str(REFERENCE))

from omp_guardrails import cli  # noqa: E402
from omp_guardrails.python_policy import (  # noqa: E402
    fanout,
    quality,
    scalability,
    subprocess_timeouts,
)

CHECKERS = {"quality": quality, "scalability": scalability, "fanout": fanout}


def merged_settings(base: dict, overrides: dict) -> dict:
    """Override one level deep, so nested limit sections keep their other keys."""
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


class ShopRepo:
    """A temporary Git work tree holding the fixture's files plus a case's changes."""

    def __init__(self, test: unittest.TestCase, files: dict | None = None, settings: dict | None = None):
        directory = tempfile.TemporaryDirectory()
        test.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        subprocess.run(["git", "init", "-q", str(self.path)], check=True)
        for relpath, text in {**FIXTURE["files"], **(files or {})}.items():
            target = self.path / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.profile = copy.deepcopy(FIXTURE["profile"])
        python = self.profile["guardrails"]["python"]
        self.profile["guardrails"]["python"] = merged_settings(python, settings or {})

    def run(self, checker: str, *argv: str) -> list:
        return CHECKERS[checker].run(self.path, self.profile, list(argv))


def rules_at(findings: list) -> set[tuple[str, str]]:
    return {(item.rule_id, item.path) for item in findings}


class ConfiguredCleanRepoTests(unittest.TestCase):
    def test_configured_repository_passes_every_python_policy_checker(self) -> None:
        repo = ShopRepo(self)
        for checker in CHECKERS:
            with self.subTest(checker=checker):
                self.assertEqual([], repo.run(checker))

    def test_each_planted_violation_is_reported_at_its_path(self) -> None:
        for case in FIXTURE["violations"]:
            label = f"{case['rule']} {case['path']} {case['note']}".strip()
            with self.subTest(label):
                repo = ShopRepo(self, case["files"], case["settings"])
                findings = repo.run(case["checker"])
                self.assertIn((case["rule"], case["path"]), rules_at(findings), findings)
                for item in findings:
                    self.assertTrue(item.fix, item)


class FailClosedDefaultTests(unittest.TestCase):
    def test_without_settings_every_file_is_unmapped_and_fanout_asks_for_roots(self) -> None:
        repo = ShopRepo(self)
        repo.profile = {"public_entry_points": {"shop": "shop.cli:main"}}
        unmapped = {item.path for item in repo.run("quality") if item.rule_id == "PYQ100"}
        expected = {path for path in FIXTURE["files"] if path.endswith(".py")}
        self.assertEqual(expected, unmapped)
        self.assertEqual(
            [("FANOUT.general", "record:profile.md")], sorted(rules_at(repo.run("fanout")))
        )

    def test_empty_script_allowlist_rejects_every_command(self) -> None:
        repo = ShopRepo(self, settings={"script_names": []})
        self.assertIn(("PYQ101", "scripts/build-catalogue.py"), rules_at(repo.run("quality")))

    def test_process_scope_defaults_to_every_maintained_root(self) -> None:
        repo = ShopRepo(self)
        del repo.profile["guardrails"]["python"]["subprocess_wrappers"]
        found = rules_at(repo.run("quality"))
        self.assertIn(("PYQ220", "src/shop/process.py"), found)
        self.assertIn(("PYQ220", "scripts/build-catalogue.py"), found)

    def test_unset_class_limits_use_the_catalogue_contract(self) -> None:
        self.assertEqual(
            {"max_lines": 700, "max_public_methods": None, "max_init_args": 8,
             "max_instance_attributes": 12},
            quality.class_limits(quality.load_settings({})),
        )


class SettingsTests(unittest.TestCase):
    def test_banned_words_come_from_the_shipped_house_style(self) -> None:
        repo = ShopRepo(self, {"src/shop/legacy/pricing.py": "X = 1\n"})
        self.assertIn(("PYQ103", "src/shop/legacy/pricing.py"), rules_at(repo.run("quality")))
        approved = ShopRepo(
            self,
            {"src/shop/legacy/pricing.py": "X = 1\n"},
            {"generic_name_allowlist": ["src/shop/legacy"]},
        )
        self.assertEqual([], approved.run("quality"))

    def test_house_style_exception_with_adr_drops_the_finding(self) -> None:
        repo = ShopRepo(self, {"src/shop/utils.py": "X = 1\n"})
        repo.profile["house_style"] = {
            "exceptions": [{"rule": "PYQ104", "path": "src/shop/utils.py", "adr": "adr/0007.md"}]
        }
        self.assertEqual([], repo.run("quality"))

    def test_glob_roots_and_data_label_directories(self) -> None:
        files = {"lab/stages/EU-West/pricing/tool/src/run_stage.py": "X = 1\n"}
        settings = {"maintained_roots": ["src/shop", "scripts", "tests", ".agents/skills",
                                         "lab/stages/*/*/tool/src"]}
        self.assertEqual([], ShopRepo(self, files, settings).run("quality"))
        files = {"src/shop/regions/EU-West/rates.py": "X = 1\n"}
        flagged = ShopRepo(self, files).run("quality")
        self.assertIn(("PYQ105", "src/shop/regions/EU-West/rates.py"), rules_at(flagged))
        labelled = ShopRepo(self, files, {"data_label_dirs": ["src/shop/regions/*"]})
        self.assertEqual([], labelled.run("quality"))

    def test_excluded_paths_and_tool_caches_are_not_maintained_source(self) -> None:
        files = {"vendor/upstream.py": "X = 1\n", ".venv/lib/site.py": "X = 1\n"}
        repo = ShopRepo(self, files, {"excluded_paths": ["vendor"]})
        self.assertEqual([], repo.run("quality"))

    def test_path_arguments_limit_the_scan(self) -> None:
        repo = ShopRepo(self, {"lib/orphan.py": "X = 1\n", "src/shop/utils.py": "X = 1\n"})
        self.assertEqual({("PYQ104", "src/shop/utils.py")}, rules_at(repo.run("quality", "src/shop")))

    def test_contract_json_record_binding_must_match_exactly(self) -> None:
        source = "from typing import Any\n\n\ndef price(pricer: int, raw: dict[str, Any]) -> int:\n    return 1\n"
        repo = ShopRepo(self, {"src/shop/contracts.py": source})
        self.assertIn(("PYQ231", "src/shop/contracts.py"), rules_at(repo.run("quality")))

    def test_loop_depth_limit_and_scope_are_configurable(self) -> None:
        nested = {"src/shop/pairs.py": "def pairs(rows):\n    for a in rows:\n        for b in a:\n            yield b\n"}
        self.assertEqual([], ShopRepo(self, nested).run("scalability"))
        strict = ShopRepo(self, nested, {"max_loop_depth": 1})
        self.assertIn(("PYS250", "src/shop/pairs.py"), rules_at(strict.run("scalability")))
        unscoped = ShopRepo(self, nested, {"max_loop_depth": 1, "loop_roots": ["scripts"]})
        self.assertEqual([], unscoped.run("scalability"))

    def test_function_bodies_reset_loop_depth(self) -> None:
        source = (
            "def outer(rows):\n    for row in rows:\n        for cell in row:\n"
            "            def inner(items):\n                for item in items:\n"
            "                    yield item\n            yield from inner(cell)\n"
        )
        self.assertEqual([], ShopRepo(self, {"src/shop/walk.py": source}).run("scalability"))

    def test_fanout_counts_first_party_modules_and_ignores_stdlib(self) -> None:
        source = "import os\nimport json\nfrom shop import orders, process\nfrom . import cli\n"
        repo = ShopRepo(self, {"src/shop/hub.py": source}, {"fanout": {"architectural_max": 2}})
        findings = repo.run("fanout")
        self.assertEqual({("FANOUT.general", "src/shop/hub.py")}, rules_at(findings))
        self.assertIn("shop.cli, shop.orders, shop.process", findings[0].message)

    def test_fanout_root_packages_default_to_public_entry_point_packages(self) -> None:
        source = "import shop.orders\nimport shop.process\n"
        settings = {"fanout": {"architectural_max": 1}}
        repo = ShopRepo(self, {"src/shop/hub.py": source}, settings)
        repo.profile["public_entry_points"] = {}
        self.assertIn("fanout.root_packages", repo.run("fanout")[0].message)


class CliTests(unittest.TestCase):
    def write_profile(self, repo: ShopRepo) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        record = Path(directory.name)
        profile = record / "profile.md"
        profile.write_text(f"---\n{json.dumps(repo.profile)}\n---\n# Test profile\n")
        return record

    def test_cli_dispatches_each_python_subcommand(self) -> None:
        repo = ShopRepo(self, {"src/shop/utils.py": "X = 1\n"})
        record = self.write_profile(repo)
        expected = {"python-quality": 1, "python-scalability": 0, "python-fanout": 0}
        for name, code in expected.items():
            with self.subTest(name), contextlib.redirect_stdout(io.StringIO()) as out:
                argv = [name, "--repo", str(repo.path), "--record", str(record)]
                self.assertEqual(code, cli.main(argv))
            if code:
                self.assertIn("src/shop/utils.py: PYQ104:", out.getvalue())


HYGIENE_PROFILE = {
    "layout": {"test_file_patterns": ["test_*.py"]},
    "tiers": {"t2": {"location": "tests/boundary"}},
}


class SubprocessTimeoutTests(unittest.TestCase):
    """TEST.subprocess-timeouts over the profile's Python test files."""

    def repo(self, files: dict[str, str]) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name)
        subprocess.run(["git", "init", "-q", str(path)], check=True)
        for relpath, text in files.items():
            target = path / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        return path

    def check(self, files: dict[str, str], profile: dict = HYGIENE_PROFILE) -> list:
        return subprocess_timeouts.run(self.repo(files), profile, [])

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
        self.assertEqual(self.check({"tests/unit/test_proc.py": bounded}), [])

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
        self.assertEqual(
            sorted(item.message.split(":", 1)[0] for item in findings),
            ["line 10", "line 15", "line 20", "line 6"],
        )
        self.assertTrue(all(item.rule_id == "TEST.subprocess-timeouts" for item in findings))
        self.assertIn("positive timeout", findings[0].fix)

    def test_only_test_files_are_checked_and_conftest_counts(self) -> None:
        call = "import subprocess\nsubprocess.run(['true'])\n"
        findings = self.check(
            {"src/shop/process.py": call, "tests/conftest.py": call, "tests/unit/helper.py": call}
        )
        self.assertEqual(rules_at(findings), {("TEST.subprocess-timeouts", "tests/conftest.py")})

    def test_unparsable_test_file_is_reported(self) -> None:
        findings = self.check({"tests/unit/test_bad.py": "def test_x(:\n"})
        self.assertEqual(rules_at(findings), {("TEST.subprocess-timeouts", "tests/unit/test_bad.py")})

    def test_cli_runs_python_test_hygiene(self) -> None:
        path = self.repo({"tests/unit/test_proc.py": "import subprocess\nsubprocess.call(['x'])\n"})
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        record = Path(directory.name)
        (record / "profile.md").write_text(f"---\n{json.dumps(HYGIENE_PROFILE)}\n---\n")
        argv = ["python-test-hygiene", "--repo", str(path), "--record", str(record)]
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(1, cli.main(argv))
        self.assertIn("tests/unit/test_proc.py: TEST.subprocess-timeouts: line 2:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
