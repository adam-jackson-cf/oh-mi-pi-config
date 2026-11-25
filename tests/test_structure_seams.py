from __future__ import annotations

import contextlib
import copy
import io
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

from guardrails.host.process import run_attached

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import config, write_config
from test_structure import FIXTURES

from guardrails.commands import cli
from guardrails.commands.structure_naming import layout, naming, path_check
from guardrails.foundation.findings import Finding, remediations

pytestmark = pytest.mark.seam


CLEAN = json.loads((FIXTURES / "clean-repo.json").read_text())


def rule_paths(findings: list[Finding], rule_id: str) -> set[str]:
    return {finding.path for finding in findings if finding.rule_id == rule_id}


class CheckerCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-structure-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        run_attached(["git", "init", "-q", str(self.repo)], check=True, timeout=60)
        self.repo_config = copy.deepcopy(CLEAN["config"])
        for path, text in CLEAN["files"].items():
            self.write(path, text)
        run_attached(["git", "-C", str(self.repo), "add", "."], check=True, timeout=60)
        run_attached(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "baseline",
            ],
            check=True,
            timeout=60,
        )

    def commit_all(self, message: str) -> None:
        run_attached(["git", "-C", str(self.repo), "add", "."], check=True, timeout=60)
        run_attached(
            [
                "git",
                "-C",
                str(self.repo),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                message,
            ],
            check=True,
            timeout=60,
        )

    def write(self, path: str, text: str = "") -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def findings(
        self, module: ModuleType, argv: list[str] | None = None
    ) -> set[tuple[str, str]]:
        return {
            (item.rule_id, item.path)
            for item in module.run(self.repo, self.repo_config, argv or [])
        }

    def all_findings(self) -> set[tuple[str, str]]:
        return self.findings(layout) | self.findings(naming) | self.findings(path_check)


class CheckerTests(CheckerCase):
    def test_clean_repository_has_no_findings(self) -> None:
        assert self.all_findings() == set()

    def test_layout_allowlists_report_folder_placement(self) -> None:
        self.write("scratch/notes.txt")
        self.write("lab/cache/run.json")
        self.write("src/orders/extras/module.py")
        self.write("src/orders/engine/batching/queue.py")
        self.write("src/orders/engine/stages/deep/nested/step.py")
        self.write("lab/workflows/team/broken/run.yaml")
        self.write("lab/workflows/loose.yaml")
        assert self.findings(layout) == {
            ("STRUCT.folder-placement", "scratch/"),
            ("STRUCT.folder-placement", "lab/cache"),
            ("STRUCT.folder-placement", "src/orders/extras/"),
            ("STRUCT.folder-placement", "src/orders/engine/batching/"),
            ("STRUCT.folder-placement", "src/orders/engine/stages/deep/nested"),
            ("STRUCT.folder-placement", "lab/workflows/team/broken/"),
            ("STRUCT.folder-placement", "lab/workflows/loose.yaml"),
        }

    def test_layout_requires_a_root_allowlist_and_reports_forbidden_imports(
        self,
    ) -> None:
        self.write("src/orders/contract/fetch.py", "import requests.adapters\n")
        assert self.findings(layout) == {
            ("STRUCT.dependency-direction", "src/orders/contract/fetch.py")
        }
        del self.repo_config["checkers"]["layout"]["root_files"]
        assert (
            "STRUCT.folder-placement",
            ".agentrails/config.json#checkers",
        ) in self.findings(layout)

    def test_naming_reports_house_style_violations(self) -> None:
        planted = {
            "src/orders/engine/utils.py": "",
            "src/orders/engine/runner_v2.py": "",
            "src/orders/engine/engine.py": "",
            "src/orders/engine/engine-core/entry.py": "",
            "src/orders/engine/BadName.py": "",
            "src/orders/engine/shapes.py": "class bad_shape: ...\n\n\ndef BuildShape(): ...\n",
            "src/orders/engine/fallback.py": "",
        }
        for path, text in planted.items():
            self.write(path, text)
        self.write("docs/Design.md")
        assert self.findings(naming) == {("NAME.conventions", path) for path in planted}

    def test_naming_reports_test_file_names(self) -> None:
        self.write("tests/unit/test_issue_1234.py")
        self.write("tests/unit/order_totals_test.py")
        self.write("tests/unit/test_.py")
        assert self.findings(naming) == {
            ("NAME.test-files", "tests/unit/test_issue_1234.py"),
            ("NAME.test-files", "tests/unit/test_.py"),
        }

    def test_versions_are_allowed_in_exempt_paths_and_standalone_folders(self) -> None:
        self.write("docs/records/v2/review-v3.md")
        self.write("src/orders/engine/stages/v2.json")
        assert self.findings(naming) == set()
        self.repo_config["checkers"]["naming"]["standalone_versions"] = False
        assert self.findings(naming) == {
            ("NAME.conventions", "src/orders/engine/stages/v2.json")
        }

    def test_path_check_reports_ownership_test_placement_and_direction(self) -> None:
        planted = {
            "lab/workflows/team/nightly/notes.md": "",
            "tests/boundary/test_payments_retry.py": "",
            "tests/fixtures/test_sample_order.py": "",
            "tests/scheduled/test_order_soak.py": "",
            "src/orders/engine/checks.py": "import pytest\n\ndef test_engine_runs():\n    assert True\n",
            "src/orders/engine/upward.py": "from orders.cli import main\n",
            "src/orders/contract/reach.py": "from ..engine import runner\n",
        }
        for path, text in planted.items():
            self.write(path, text)
        assert self.findings(path_check, list(planted)) == {
            ("STRUCT.folder-placement", "lab/workflows/team/nightly/notes.md"),
            ("TEST.placement", "tests/boundary/test_payments_retry.py"),
            ("STRUCT.folder-placement", "tests/fixtures/test_sample_order.py"),
            ("TEST.placement", "tests/fixtures/test_sample_order.py"),
            ("STRUCT.folder-placement", "tests/scheduled/test_order_soak.py"),
            ("TEST.placement", "src/orders/engine/checks.py"),
            ("STRUCT.dependency-direction", "src/orders/engine/upward.py"),
            ("STRUCT.dependency-direction", "src/orders/contract/reach.py"),
        }

    def test_path_check_only_reports_framework_backed_collectable_tests(self) -> None:
        cases = {
            "production helper": (
                "def test_patterns(repo_config):\n    return repo_config\n",
                False,
            ),
            "pytest function": (
                "import pytest\n\ndef test_x():\n    assert True\n",
                True,
            ),
            "pytest class": (
                "import pytest\n\nclass TestOrders:\n    def test_x(self):\n        assert True\n",
                True,
            ),
            "unittest class": (
                "import unittest\n\nclass Orders(unittest.TestCase):\n"
                "    def test_x(self):\n        self.assertTrue(True)\n",
                True,
            ),
            "imported TestCase": (
                "from unittest import TestCase\n\nclass Orders(TestCase):\n"
                "    def test_x(self):\n        self.assertTrue(True)\n",
                True,
            ),
            "aliased TestCase": (
                "from unittest import TestCase as Case\n\nclass Orders(Case):\n"
                "    def test_x(self):\n        self.assertTrue(True)\n",
                True,
            ),
            "aliased unittest": (
                "import unittest as unit\n\nclass Orders(unit.TestCase):\n"
                "    def test_x(self):\n        self.assertTrue(True)\n",
                True,
            ),
            "nested function": (
                "import pytest\n\ndef patterns():\n    def test_x():\n        assert True\n",
                False,
            ),
            "ordinary class": (
                "import pytest\n\nclass Patterns:\n    def test_node(self):\n        return True\n",
                False,
            ),
            "nested class helper": (
                "import pytest\n\ndef patterns():\n"
                "    class TestOrders:\n        def test_x(self):\n            assert True\n",
                False,
            ),
            "nested method helper": (
                "import pytest\n\nclass TestOrders:\n    def patterns(self):\n"
                "        def test_x():\n            assert True\n",
                False,
            ),
        }
        path = "src/orders/engine/checks.py"
        for name, (source, collected) in cases.items():
            with self.subTest(case=name):
                self.write(path, source)
                expected = {("TEST.placement", path)} if collected else set()
                assert self.findings(path_check, [path]) == expected

    def test_path_check_reports_a_directory_outside_git_as_a_gate_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="omp-path-check-") as scratch:
            found = path_check.run(Path(scratch), {}, [])
        assert [(finding.rule_id, finding.path) for finding in found] == [
            ("GATE.path-check", ".")
        ]

    def test_path_check_refuses_paths_outside_the_repository(self) -> None:
        assert self.findings(path_check, ["../outside.py", "/etc/hosts"]) == {
            ("STRUCT.folder-placement", "../outside.py"),
            ("STRUCT.folder-placement", "/etc/hosts"),
        }

    def package_placement(
        self, module: ModuleType, argv: list[str] | None = None
    ) -> set[tuple[str, str]]:
        rules = {"STRUCT.runtime-data-placement", "STRUCT.entry-point-placement"}
        return {item for item in self.findings(module, argv) if item[0] in rules}

    def test_runtime_data_and_deep_entry_modules_in_a_package_are_reported(
        self,
    ) -> None:
        self.write("src/orders/engine/pricing.yaml")
        self.write("src/orders/engine/stages/__init__.py")
        self.write("src/orders/engine/stages/__main__.py")
        self.write("src/orders/__main__.py")
        self.write("src/orders/cli/cli.py")
        self.write("lab/workflows/team/nightly/settings.json")
        self.write("tests/fixtures/orders.json")
        data = ("STRUCT.runtime-data-placement", "src/orders/engine/pricing.yaml")
        entry = ("STRUCT.entry-point-placement", "src/orders/engine/stages/__main__.py")
        for module in (layout, path_check):
            assert self.package_placement(module) == {data, entry}
        self.repo_config["exceptions"] = [
            {
                "rule": "STRUCT.entry-point-placement",
                "path": "src/orders/engine/stages",
                "reason": "internal tool the engine runs; no user invokes it",
            }
        ]
        for module in (layout, path_check):
            assert self.package_placement(module) == {data}

    def test_package_placement_judges_only_added_paths(self) -> None:
        self.write("src/orders/engine/legacy.yaml")
        self.commit_all("legacy data")
        self.write("src/orders/engine/added.yaml")
        for module in (layout, path_check):
            assert self.package_placement(
                module, ["--event", "edit", "src/orders/engine"]
            ) == {("STRUCT.runtime-data-placement", "src/orders/engine/added.yaml")}

    def test_record_exceptions_suppress_only_their_rule_and_path(self) -> None:
        self.write("src/orders/engine/utils.py")
        self.write("src/orders/cli/helpers.py")
        self.repo_config["exceptions"] = [
            {
                "rule": "NAME.conventions",
                "path": "src/orders/engine",
                "reason": "owner approval",
            }
        ]
        assert self.findings(naming) == {
            ("NAME.conventions", "src/orders/cli/helpers.py")
        }

    def test_glob_exception_suppresses_only_the_named_subcheck(self) -> None:
        path = "src/orders/engine/performance/test_performance_v2.py"
        other = "src/orders/engine/performance/test_performance_v3.py"
        exception = {
            "rule": "NAME.conventions",
            "check": "child-repeats-parent",
            "path": "src/**/test_performance_v2.py",
            "reason": "owner approval",
        }
        self.repo_config["exceptions"] = [exception]
        for candidate in (path, other):
            self.write(candidate)
        found = naming.run(self.repo, self.repo_config, [path, other])
        assert not any(
            item.path == path and item.check == "child-repeats-parent" for item in found
        )
        assert any(
            item.path == path and item.rule_id == "NAME.conventions" for item in found
        )
        assert any(
            item.path == other and item.check == "child-repeats-parent"
            for item in found
        )
        del exception["reason"]
        assert any(
            item.check == "child-repeats-parent"
            for item in naming.run(self.repo, self.repo_config, [path])
        )


class CheckerCliTests(CheckerCase):
    def write_repo_config(self) -> None:
        write_config(self.repo, config(**self.repo_config))

    def test_rails_state_folders_are_never_scanned(self) -> None:
        self.write_repo_config()
        self.write(".proofs/run-state.md", "setup-repo-proofs run state\n")
        for name in ("structure", "naming", "path-check"):
            with self.subTest(name=name):
                assert self.run_cli(name, "--all") == (0, f"{name}: ok\n")

    def test_cli_runs_the_checkers_against_the_repo_config(self) -> None:
        self.write_repo_config()
        for name in ("structure", "naming", "path-check"):
            with self.subTest(name=name):
                assert self.run_cli(name) == (0, f"{name}: ok\n")
        self.write("src/orders/engine/misc.py")
        code, out = self.run_cli("naming")
        assert code == 1
        assert "src/orders/engine/misc.py: NAME.conventions:" in out

    def run_cli(self, name: str, *paths: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main([
                name,
                "--repo",
                str(self.repo),
                *paths,
            ])
        return code, out.getvalue()

    def test_path_check_reports_one_actionable_line_per_named_path(self) -> None:
        self.write_repo_config()
        self.write("src/orders/engine/misc.py")
        assert self.run_cli("path-check", "src/orders/engine/runner.py")[0] == 0
        code, out = self.run_cli("path-check", "src/orders/engine/misc.py")
        assert code == 1
        *lines, guidance = out.splitlines()
        assert lines
        assert all(line.startswith("src/orders/engine/misc.py: ") for line in lines)
        assert guidance.startswith("remediation NAME.conventions: root cause: ")

    def test_native_import_direction_checks_non_python_sources(self) -> None:
        source = "src/orders/engine/runner.ts"
        self.write(source, "export const ready = true;\n")
        scopes: tuple[dict[str, Any], ...] = (
            {"scan": ["src/**/*.ts"]},
            {"source_root": "src"},
        )
        for scope, exit_code in itertools.product(scopes, (1, 0)):
            self.repo_config["structure"]["import_direction"] = {
                **scope,
                "native": {
                    "command": [
                        sys.executable,
                        "-c",
                        f"raise SystemExit({exit_code})",
                    ],
                    "timeout_s": 5,
                    "cwd": ".",
                },
            }
            self.write_repo_config()
            for name in ("path-check", "structure"):
                with self.subTest(scope=scope, exit_code=exit_code, command=name):
                    code, out = self.run_cli(name, source)
                    assert code == exit_code
                    if exit_code:
                        assert f"{source}: STRUCT.dependency-direction:" in out
                    else:
                        assert out == f"{name}: ok\n"

    def test_native_import_direction_ignores_paths_outside_scan(self) -> None:
        self.repo_config["structure"]["import_direction"] = {
            "scan": ["src/**/*.ts"],
            "native": {
                "command": [sys.executable, "-c", "raise SystemExit(1)"],
                "timeout_s": 5,
            },
        }
        self.write_repo_config()
        for name in ("path-check", "structure"):
            with self.subTest(command=name):
                assert self.run_cli(name, "src/orders/engine/runner.py") == (
                    0,
                    f"{name}: ok\n",
                )

    def test_native_import_direction_checks_inventory_without_arguments(self) -> None:
        source = "src/orders/engine/runner.ts"
        self.write(source, "export const ready = true;\n")
        self.repo_config["structure"]["import_direction"] = {
            "scan": ["src/**/*.ts"],
            "native": {
                "command": [sys.executable, "-c", "raise SystemExit(1)"],
                "timeout_s": 5,
            },
        }
        self.write_repo_config()
        for name in ("path-check", "structure"):
            with self.subTest(command=name):
                code, out = self.run_cli(name)
                assert code == 1
                assert out.splitlines() == [
                    f"{source}: STRUCT.dependency-direction: repository import contracts failed; "
                    f"run {sys.executable} -c raise SystemExit(1) and remove forbidden edges",
                    "remediation STRUCT.dependency-direction: "
                    + remediations()["STRUCT.dependency-direction"].render(),
                ]

    def test_check_rejects_automatic_whole_repo_naming_and_structure(self) -> None:
        for step in ("structure", "naming", "path-check"):
            with self.subTest(step=step):
                self.repo_config["checkers"]["check"] = {"run": [step]}
                self.write_repo_config()
                code, out = self.run_cli("check")
                assert (code, out) == (2, ""), "config-check refuses the unknown step"

    def test_check_steps_come_from_a_fixed_list_and_record_findings_always_show(
        self,
    ) -> None:
        self.repo_config["checkers"]["check"] = {
            "run": ["diff-check"],
            "exclude": ["**"],
        }
        self.write_repo_config()
        assert self.run_cli("check") == (2, ""), "config-check refuses the unknown step"
        from guardrails.commands.check import whole_repo

        _, problems = whole_repo.configured_steps({
            "checkers": {"check": {"run": ["diff-check"]}}
        })
        assert [item.path for item in problems] == [".agentrails/config.json#checkers"]
        assert "checkers.check.run names unknown steps" in problems[0].message

        steps, problems = whole_repo.configured_steps({"checkers": {"check": {}}})
        assert (steps, problems) == (list(whole_repo.DEFAULT_STEPS), [])
        assert "adapter lint" in steps
        self.repo_config["checkers"]["check"] = {}
        self.write_repo_config()
        code, out = self.run_cli("check", "src")
        assert code == 1
        assert "check takes no paths or options" in out

    def test_check_leaves_out_the_steps_the_repo_checkers_own(self) -> None:
        from guardrails.commands.check import whole_repo

        owned = {
            "checkers": {
                "check": {"repo": {"python-fanout": ["src/orders/cli/main.py"]}}
            }
        }
        steps, problems = whole_repo.configured_steps(owned)
        assert problems == []
        assert steps == [
            step for step in whole_repo.DEFAULT_STEPS if step != "python-fanout"
        ]
        self.repo_config["checkers"]["check"] = {
            "run": ["python-subprocess-timeouts"],
            "repo": {
                "naming": ["src/orders/cli/main.py"],
                "structure": ["src/orders/cli/main.py"],
            },
        }
        self.write_repo_config()
        assert self.run_cli("check") == (0, "check: ok\n")

    def test_check_refuses_a_repo_owned_step_the_harness_cannot_trust(self) -> None:
        cases = {
            "unknown step": (
                {"repo": {"python-lint": ["checks/lint.py"]}},
                "checkers.check.repo names 'python-lint'",
            ),
            "harness path check": (
                {"repo": {"path-check": ["src/orders/cli/main.py"]}, "exclude": ["**"]},
                "checkers.check.repo names 'path-check', which the repo cannot own",
            ),
            "listed twice": (
                {
                    "run": ["python-fanout"],
                    "repo": {"python-fanout": ["src/orders/cli/main.py"]},
                },
                "checkers.check.run lists repo-owned steps ['python-fanout']",
            ),
            "missing checker": (
                {"repo": {"naming": ["src/orders/cli/main.py", "checks/absent.py"]}},
                "checkers.check.repo.naming names checks/absent.py, which is not in the repository",
            ),
            "not a map": ({"repo": ["naming"]}, "checkers.check.repo must map steps"),
            "single path": (
                {"repo": {"naming": "src/orders/cli/main.py"}},
                "checkers.check.repo.naming must list the repo checker files",
            ),
            "no checkers": (
                {"repo": {"naming": []}},
                "checkers.check.repo.naming must list",
            ),
        }
        self.repo_config["checkers"].pop("naming", None)
        for name, (check, expected) in cases.items():
            with self.subTest(name=name):
                self.repo_config["checkers"]["check"] = check
                self.write_repo_config()
                code, out = self.run_cli("check")
                assert code == 1
                assert (
                    f".agentrails/config.json#checkers: CONFIG.complete: {expected}"
                    in out
                )

    def test_check_rejects_settings_only_repo_owned_steps_read(self) -> None:
        owned = {
            "python-quality": ["src/orders/cli/main.py"],
            "python-fanout": ["src/orders/cli/main.py"],
            "python-scalability": ["src/orders/cli/main.py"],
        }
        self.repo_config["checkers"]["python"] = {"maintained_roots": ["src"]}
        self.repo_config["checkers"]["check"] = {"run": ["adapter lint"], "repo": owned}
        self.write_repo_config()
        code, out = self.run_cli("check")
        assert code == 1
        assert "checkers.python configures only repo-owned steps" in out
        self.repo_config["checkers"]["check"]["run"].append(
            "python-subprocess-timeouts"
        )
        self.write_repo_config()
        assert "checkers.python configures" not in self.run_cli("check")[1]
        self.repo_config["checkers"]["naming"] = {"skip": ["vendor/**"]}
        self.repo_config["checkers"]["check"] = {
            "run": ["python-subprocess-timeouts"],
            "repo": {"naming": ["src/orders/cli/main.py"]},
        }
        self.write_repo_config()
        assert self.run_cli("check") == (0, "check: ok\n")


class ImportGraphTests(unittest.TestCase):
    RULES = {
        "STRUCT.tier-readership",
        "STRUCT.single-owner-placement",
        "STRUCT.sibling-isolation",
    }
    FIXTURE = json.loads((FIXTURES / "import-graph.json").read_text())

    def make_repo(self, files: dict[str, str], repo_config: dict[str, Any]) -> Path:
        scratch = tempfile.TemporaryDirectory(prefix="omp-import-graph-")
        self.addCleanup(scratch.cleanup)
        repo = Path(scratch.name) / "repo"
        run_attached(["git", "init", "-q", str(repo)], check=True, timeout=60)
        write_config(repo, config(**repo_config))
        for path, text in files.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return repo

    def test_graph_rules_have_violating_and_conforming_public_outcomes(self) -> None:
        outcomes = ("violating", "conforming")
        for case, outcome in itertools.product(self.FIXTURE["cases"], outcomes):
            with self.subTest(case=case["name"], outcome=outcome):
                repo_config = copy.deepcopy(self.FIXTURE["config"])
                repo = self.make_repo(case[outcome], repo_config)
                expected = set(case["paths"]) if outcome == "violating" else set()
                for checker in (layout, path_check):
                    findings = checker.run(repo, repo_config, ["--all"])
                    assert rule_paths(findings, case["rule"]) == expected
                for command in ("structure", "path-check"):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        cli.main([
                            command,
                            "--repo",
                            str(repo),
                            "--all",
                        ])
                    assert (case["rule"] in output.getvalue()) == bool(expected)

    def test_no_import_settings_means_no_graph_findings(self) -> None:
        case = self.FIXTURE["cases"][0]
        repo_config = copy.deepcopy(self.FIXTURE["config"])
        del repo_config["structure"]["import_direction"]
        repo = self.make_repo(case["violating"], repo_config)
        for checker in (layout, path_check):
            assert (
                not {f.rule_id for f in checker.run(repo, repo_config, [])} & self.RULES
            )

    def test_scan_excludes_python_outside_its_scope(self) -> None:
        repo_config = copy.deepcopy(self.FIXTURE["config"])
        repo_config["structure"]["import_direction"]["scan"] = ["src/orders/commands"]
        files = dict(self.FIXTURE["cases"][0]["violating"])
        files["src/orders/shared/broken.py"] = "this is not Python!\n"
        repo = self.make_repo(files, repo_config)
        for checker in (layout, path_check):
            assert (
                not {f.rule_id for f in checker.run(repo, repo_config, [])} & self.RULES
            )

    def test_unused_shared_tier_fails_but_driving_tier_does_not(self) -> None:
        repo_config = copy.deepcopy(self.FIXTURE["config"])
        repo = self.make_repo(
            {
                "src/orders/shared/model.py": "VALUE = 1\n",
                "src/orders/commands/export.py": "VALUE = 1\n",
            },
            repo_config,
        )
        for checker in (layout, path_check):
            found = checker.run(repo, repo_config, [])
            assert {(f.rule_id, f.path) for f in found if f.rule_id in self.RULES} == {
                ("STRUCT.tier-readership", "src/orders/shared")
            }


if __name__ == "__main__":
    unittest.main()
