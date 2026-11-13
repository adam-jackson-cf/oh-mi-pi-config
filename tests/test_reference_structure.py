"""House-style verdicts and the structure, naming and path checkers of the guardrails reference."""

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
FIXTURES = ROOT / "tests/fixtures/reference/structure"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails import cli  # noqa: E402
from omp_guardrails.config import house_style  # noqa: E402
from omp_guardrails.structure import layout, naming, paths  # noqa: E402

CLEAN = json.loads((FIXTURES / "clean-repo.json").read_text())
VERDICTS = json.loads((FIXTURES / "house-style-verdicts.json").read_text())


class HouseStyleVerdictTests(unittest.TestCase):
    """The shared, stack-neutral verdict set every house-style reader must reproduce."""

    def test_fixture_targets_the_shipped_version_and_sources_are_recorded(self) -> None:
        data = house_style.load_house_style()
        self.assertEqual(VERDICTS["house_style_version"], data["version"])
        self.assertGreaterEqual(len(data["sources"]), 5)
        self.assertEqual(data["banned_words"], sorted(set(data["banned_words"])))
        self.assertRegex(house_style.digest(), r"^[0-9a-f]{64}$")

    def test_banned_words(self) -> None:
        for word in VERDICTS["banned_words"]["banned"]:
            self.assertTrue(house_style.is_banned_word(word), word)
        for word in VERDICTS["banned_words"]["allowed"]:
            self.assertFalse(house_style.is_banned_word(word), word)

    def test_casing(self) -> None:
        for case in VERDICTS["casing"]:
            with self.subTest(case=case):
                self.assertEqual(
                    house_style.matches_casing(case["name"], case["casing"]), case["matches"]
                )

    def test_versions(self) -> None:
        for case in VERDICTS["versions"]:
            with self.subTest(case=case):
                problem = house_style.version_problem(
                    case["segment"],
                    last=case["last"],
                    standalone_allowed=case["standalone_allowed"],
                )
                self.assertEqual(problem is not None, case["violates"])

    def test_test_names(self) -> None:
        for case in VERDICTS["test_names"]:
            with self.subTest(case=case):
                problems = house_style.test_name_problems(case["name"])
                self.assertEqual(bool(problems), case["violates"], problems)

    def test_test_roots(self) -> None:
        roots = house_style.load_house_style()["test_roots"]
        expected = VERDICTS["test_roots"]
        self.assertEqual(
            (roots["root"], roots["fixtures_dir"]), (expected["root"], expected["fixtures_dir"])
        )
        self.assertIn(expected["python"], roots["file_patterns"]["python"])
        self.assertIn(expected["typescript"], roots["file_patterns"]["typescript"])


class CheckerTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-structure-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        self.record = Path(scratch.name).resolve() / "record"
        self.record.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.profile = copy.deepcopy(CLEAN["profile"])
        for path, text in CLEAN["files"].items():
            self.write(path, text)

    def write(self, path: str, text: str = "") -> None:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def findings(self, module, argv: list[str] | None = None) -> set[tuple[str, str]]:
        return {
            (item.rule_id, item.path) for item in module.run(self.repo, self.profile, argv or [])
        }

    def all_findings(self) -> set[tuple[str, str]]:
        return self.findings(layout) | self.findings(naming) | self.findings(paths)

    def test_clean_repository_has_no_findings(self) -> None:
        self.assertEqual(self.all_findings(), set())

    def test_layout_allowlists_report_folder_placement(self) -> None:
        self.write("scratch/notes.txt")
        self.write("lab/cache/run.json")
        self.write("src/orders/extras/module.py")
        self.write("src/orders/engine/batching/queue.py")
        self.write("src/orders/engine/stages/deep/nested/step.py")
        self.write("lab/workflows/team/broken/run.yaml")
        self.write("lab/workflows/loose.yaml")
        self.assertEqual(
            self.findings(layout),
            {
                ("STRUCT.folder-placement", "scratch/"),
                ("STRUCT.folder-placement", "lab/cache"),
                ("STRUCT.folder-placement", "src/orders/extras/"),
                ("STRUCT.folder-placement", "src/orders/engine/batching/"),
                ("STRUCT.folder-placement", "src/orders/engine/stages/deep/nested"),
                ("STRUCT.folder-placement", "lab/workflows/team/broken/"),
                ("STRUCT.folder-placement", "lab/workflows/loose.yaml"),
            },
        )

    def test_layout_requires_a_root_allowlist_and_reports_forbidden_imports(self) -> None:
        self.write("src/orders/contract/fetch.py", "import requests.adapters\n")
        self.assertEqual(
            self.findings(layout), {("STRUCT.dependency-direction", "src/orders/contract/fetch.py")}
        )
        del self.profile["guardrails"]["structure"]["root_files"]
        self.assertIn(("STRUCT.folder-placement", "record:profile.md"), self.findings(layout))

    def test_naming_reports_house_style_violations(self) -> None:
        planted = {
            "src/orders/engine/utils.py": "",
            "src/orders/engine/runner_v2.py": "",
            "src/orders/engine/engine.py": "",
            "src/orders/engine/BadName.py": "",
            "src/orders/engine/shapes.py": "class bad_shape: ...\n\n\ndef BuildShape(): ...\n",
            "docs/Design.md": "",
        }
        for path, text in planted.items():
            self.write(path, text)
        self.assertEqual(self.findings(naming), {("NAME.conventions", path) for path in planted})

    def test_naming_reports_test_file_names(self) -> None:
        self.write("tests/unit/test_issue_1234.py")
        self.write("tests/unit/order_totals_test.py")
        self.assertEqual(
            self.findings(naming),
            {
                ("NAME.test-files", "tests/unit/test_issue_1234.py"),
                ("NAME.test-files", "tests/unit/order_totals_test.py"),
            },
        )

    def test_versions_are_allowed_in_exempt_paths_and_standalone_folders(self) -> None:
        self.write("docs/records/v2/review-v3.md")
        self.write("src/orders/engine/stages/v2.py")
        self.assertEqual(self.findings(naming), set())
        self.profile["guardrails"]["naming"]["standalone_versions"] = False
        self.assertEqual(
            self.findings(naming), {("NAME.conventions", "src/orders/engine/stages/v2.py")}
        )

    def test_path_check_reports_ownership_test_placement_and_direction(self) -> None:
        planted = {
            "lab/workflows/team/nightly/notes.md": "",
            "tests/boundary/test_payments_retry.py": "",
            "tests/fixtures/test_sample_order.py": "",
            "tests/scheduled/test_order_soak.py": "",
            "src/orders/engine/checks.py": "def test_engine_runs():\n    assert True\n",
            "src/orders/engine/upward.py": "from orders.cli import main\n",
            "src/orders/contract/reach.py": "from ..engine import runner\n",
        }
        for path, text in planted.items():
            self.write(path, text)
        self.assertEqual(
            self.findings(paths, list(planted)),
            {
                ("STRUCT.folder-placement", "lab/workflows/team/nightly/notes.md"),
                ("TEST.placement", "tests/boundary/test_payments_retry.py"),
                ("STRUCT.folder-placement", "tests/fixtures/test_sample_order.py"),
                ("TEST.placement", "tests/fixtures/test_sample_order.py"),
                ("STRUCT.folder-placement", "tests/scheduled/test_order_soak.py"),
                ("TEST.placement", "src/orders/engine/checks.py"),
                ("STRUCT.dependency-direction", "src/orders/engine/upward.py"),
                ("STRUCT.dependency-direction", "src/orders/contract/reach.py"),
            },
        )

    def test_path_check_refuses_paths_outside_the_repository(self) -> None:
        self.assertEqual(
            self.findings(paths, ["../outside.py", "/etc/hosts"]),
            {
                ("STRUCT.folder-placement", "../outside.py"),
                ("STRUCT.folder-placement", "/etc/hosts"),
            },
        )

    def test_adr_backed_exceptions_suppress_only_their_rule_and_path(self) -> None:
        self.write("src/orders/engine/utils.py")
        self.write("src/orders/cli/helpers.py")
        self.profile["house_style"]["exceptions"] = [
            {"rule": "NAME.conventions", "path": "src/orders/engine", "adr": "ADR-0004"}
        ]
        self.assertEqual(self.findings(naming), {("NAME.conventions", "src/orders/cli/helpers.py")})

    def write_profile(self) -> None:
        text = f"---\n{json.dumps(self.profile)}\n---\n\n# Repo test profile\n"
        (self.record / "profile.md").write_text(text)

    def test_cli_runs_the_checkers_against_the_record_profile(self) -> None:
        self.write_profile()
        for name in ("structure", "naming", "path-check"):
            with self.subTest(name=name):
                self.assertEqual(self.run_cli(name), (0, f"{name}: ok\n"))
        self.write("src/orders/engine/misc.py")
        code, out = self.run_cli("naming")
        self.assertEqual(code, 1)
        self.assertIn("src/orders/engine/misc.py: NAME.conventions:", out)

    def run_cli(self, name: str, *paths: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main([name, "--repo", str(self.repo), "--record", str(self.record), *paths])
        return code, out.getvalue()

    def test_path_check_reports_one_actionable_line_per_named_path(self) -> None:
        """GATE.path-check: changed paths as arguments, exit 0 clean, one error line per file."""
        self.write_profile()
        self.write("src/orders/engine/misc.py")
        self.assertEqual(self.run_cli("path-check", "src/orders/engine/runner.py")[0], 0)
        code, out = self.run_cli("path-check", "src/orders/engine/misc.py")
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertTrue(lines and all(line.startswith("src/orders/engine/misc.py: ") for line in lines))

    def test_check_runs_the_record_steps_over_the_whole_repo_and_writes_nothing(self) -> None:
        self.profile["guardrails"]["check"] = {"run": ["structure", "naming", "path-check"]}
        self.write_profile()
        self.assertEqual(self.run_cli("check"), (0, "check: ok\n"))
        self.write("src/orders/engine/misc.py")
        self.write("vendor/third/misc.py")
        before = sorted(path for path in self.repo.rglob("*") if ".git" not in path.parts)
        code, out = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("src/orders/engine/misc.py: NAME.conventions:", out)
        self.assertIn("vendor", out)
        self.assertEqual(
            sorted(path for path in self.repo.rglob("*") if ".git" not in path.parts), before
        )
        self.profile["guardrails"]["check"]["exclude"] = ["vendor/**", "src/orders/engine/misc.py"]
        self.write_profile()
        self.assertEqual(self.run_cli("check"), (0, "check: ok\n"))

    def test_check_steps_come_from_a_fixed_list_and_record_findings_always_show(self) -> None:
        self.profile["guardrails"]["check"] = {"run": ["diff-check"], "exclude": ["**"]}
        self.write_profile()
        code, out = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("record:profile.md: PROFILE.complete: guardrails.check.run names", out)
        from omp_guardrails import whole_repo

        steps, problems = whole_repo.configured_steps({"guardrails": {"check": {}}})
        self.assertEqual((steps, problems), (list(whole_repo.DEFAULT_STEPS), []))
        self.assertIn("adapter lint", steps)
        code, out = self.run_cli("check", "src")
        self.assertEqual(code, 1)
        self.assertIn("check takes no paths or options", out)


    def test_check_leaves_out_the_steps_the_repo_checkers_own(self) -> None:
        from omp_guardrails import whole_repo

        owned = {"guardrails": {"check": {"repo": {"naming": "src/orders/cli/main.py"}}}}
        steps, problems = whole_repo.configured_steps(owned)
        self.assertEqual(problems, [])
        self.assertEqual(steps, [step for step in whole_repo.DEFAULT_STEPS if step != "naming"])
        self.write("src/orders/engine/misc.py")
        self.profile["guardrails"]["check"] = {"run": ["structure"]}
        self.profile["guardrails"]["check"]["repo"] = {"naming": "src/orders/cli/main.py"}
        self.write_profile()
        self.assertEqual(self.run_cli("check"), (0, "check: ok\n"))
        self.write("scratch/notes.txt")
        code, out = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("scratch/: STRUCT.folder-placement", out)
        self.assertNotIn("NAME.conventions", out)

    def test_check_refuses_a_repo_owned_step_the_record_cannot_trust(self) -> None:
        cases = {
            "unknown step": (
                {"repo": {"python-lint": "checks/lint.py"}},
                "guardrails.check.repo names 'python-lint'",
            ),
            "record check": (
                {"repo": {"profile-check": "checks/lint.py"}},
                "guardrails.check.repo names 'profile-check'",
            ),
            "listed twice": (
                {"run": ["naming"], "repo": {"naming": "src/orders/cli/main.py"}},
                "guardrails.check.run lists repo-owned steps ['naming']",
            ),
            "missing checker": (
                {"repo": {"naming": "checks/absent.py"}},
                "guardrails.check.repo.naming names checks/absent.py, which is not in the repository",
            ),
            "not a map": ({"repo": ["naming"]}, "guardrails.check.repo must map steps"),
        }
        for name, (check, expected) in cases.items():
            with self.subTest(name=name):
                self.profile["guardrails"]["check"] = check
                self.write_profile()
                code, out = self.run_cli("check")
                self.assertEqual(code, 1)
                self.assertIn(f"record:profile.md: PROFILE.complete: {expected}", out)

    def test_check_rejects_settings_only_repo_owned_steps_read(self) -> None:
        owned = {"python-quality": "src/orders/cli/main.py", "python-fanout": "src/orders/cli/main.py",
                 "python-scalability": "src/orders/cli/main.py"}
        self.profile["guardrails"]["python"] = {"maintained_roots": ["src"]}
        self.profile["guardrails"]["check"] = {"run": ["structure", "naming"], "repo": owned}
        self.write_profile()
        code, out = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("guardrails.python configures only repo-owned steps", out)
        self.profile["guardrails"]["check"]["run"].append("python-test-hygiene")
        self.write_profile()
        self.assertNotIn("guardrails.python configures", self.run_cli("check")[1])


if __name__ == "__main__":
    unittest.main()
