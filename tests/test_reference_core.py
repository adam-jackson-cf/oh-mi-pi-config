"""Profile loading, PROFILE.complete, findings and CLI dispatch of the guardrails reference."""

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
TEMPLATE = ROOT / "agent/skills/test-guidance/assets/templates/test-profile.md"
FIXTURES = ROOT / "tests/fixtures/reference/core"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails import cli  # noqa: E402
from omp_guardrails.config import profile as profile_module  # noqa: E402
from omp_guardrails.findings import Finding, exit_code, format_findings  # noqa: E402

CONTRACT_SUBCOMMANDS = {
    "profile-check",
    "init",
    "path-check",
    "structure",
    "naming",
    "python-quality",
    "python-fanout",
    "python-scalability",
    "python-test-hygiene",
    "canaries",
    "rule-evidence",
    "hook-check",
    "toolchain-parity",
    "gate",
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
}


def complete_profile() -> dict:
    return profile_module.parse_front_matter((FIXTURES / "complete-profile.md").read_text())


def keys_of(findings: list[Finding]) -> set[str]:
    return {finding.message.split(" ", 1)[0] for finding in findings}


class ProfileLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-core-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)

    def write_profile(self, text: str) -> None:
        path = self.repo / ".omp/test-profile.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_front_matter_json_is_returned_as_a_dict(self) -> None:
        self.write_profile((FIXTURES / "complete-profile.md").read_text())
        self.assertEqual(profile_module.load_profile(self.repo)["adapter"], "pytest")

    def test_unreadable_profiles_raise_profile_errors(self) -> None:
        cases = {
            "absent": None,
            "no front matter": "# Repo test profile\n",
            "unclosed": "---\n{}\n",
            "not json": "---\ntiers: {}\n---\n",
            "not an object": "---\n[]\n---\n",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                if text is not None:
                    self.write_profile(text)
                with self.assertRaises(profile_module.ProfileError):
                    profile_module.load_profile(self.repo)


class ProfileCompleteTests(unittest.TestCase):
    def test_complete_profile_has_no_findings(self) -> None:
        self.assertEqual(profile_module.validate_profile(complete_profile()), [])

    def test_template_front_matter_parses_and_reports_every_section(self) -> None:
        template = profile_module.parse_front_matter(TEMPLATE.read_text())
        required = set(profile_module.SCHEMA)
        self.assertEqual(set(template) - {"guardrails"}, required)
        findings = profile_module.validate_profile(template)
        reported = {key.split(".")[0].split("[")[0] for key in keys_of(findings)}
        self.assertEqual(reported, required)
        self.assertTrue(all(item.rule_id == "PROFILE.complete" for item in findings))

    def test_placeholders_and_sentinels_count_as_missing(self) -> None:
        for value in ("<command>", "n/a", "N/A", "TBD", "run TBD later", "", "none", "None: later"):
            with self.subTest(value=value):
                profile = complete_profile()
                profile["run_events"]["commit"] = value
                self.assertEqual(
                    keys_of(profile_module.validate_profile(profile)), {"run_events.commit"}
                )

    def test_absent_keys_and_empty_lists_count_as_missing(self) -> None:
        profile = complete_profile()
        del profile["guards"]["network"]
        profile["fast_tier_budget"]["machines"] = []
        profile["layout"]["test_file_patterns"] = []
        self.assertEqual(
            keys_of(profile_module.validate_profile(profile)),
            {"guards.network", "fast_tier_budget.machines", "layout.test_file_patterns"},
        )

    def test_nested_nulls_in_free_form_values_count_as_missing(self) -> None:
        cases = {
            "public_entry_points": {"ledger": None},
            "identity_artefacts": {"docs/ids.json": None},
            "structure.owners": {"directories": {"src/ledger/": None}},
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                profile = complete_profile()
                target = profile
                *parents, leaf = key.split(".")
                for step in parents:
                    target = target[step]
                target[leaf] = value
                self.assertEqual(keys_of(profile_module.validate_profile(profile)), {key})

    def test_command_notes_need_both_owner_notes_and_allow_the_lsp_note(self) -> None:
        cases = {
            "text instead of the notes object": ("repo root", {"command_notes"}),
            "missing interpretation": (
                {"working_directory": "repo root"},
                {"command_notes.interpretation"},
            ),
            "placeholder working directory": (
                {"working_directory": "<directory>", "interpretation": "exit 0 passes"},
                {"command_notes.working_directory"},
            ),
            "null lsp note": (
                {"working_directory": "repo root", "interpretation": "exit 0", "lsp": None},
                {"command_notes.lsp"},
            ),
        }
        for name, (value, expected) in cases.items():
            with self.subTest(name=name):
                profile = complete_profile()
                profile["command_notes"] = value
                self.assertEqual(keys_of(profile_module.validate_profile(profile)), expected)
        profile = complete_profile()
        profile["command_notes"]["lsp"] = [{"server": "pyright", "install": "npm i -g pyright"}]
        self.assertEqual(profile_module.validate_profile(profile), [])

    def test_approved_none_is_allowed_only_in_three_places(self) -> None:
        exemption = {"none": "owner-approved reason", "approval": "ADR-0001"}
        profile = complete_profile()
        profile["verification_surface"] = dict(exemption)
        profile["identity_artefacts"] = dict(exemption)
        profile["run_events"]["nightly_scheduler"] = dict(exemption)
        self.assertEqual(profile_module.validate_profile(profile), [])
        for key in ("coverage", "guards", "environments", "layout"):
            with self.subTest(key=key):
                elsewhere = complete_profile()
                elsewhere[key] = dict(exemption)
                self.assertIn(
                    key,
                    {k.split(".")[0] for k in keys_of(profile_module.validate_profile(elsewhere))},
                )

    def test_unapproved_or_malformed_exemptions_count_as_missing(self) -> None:
        cases = {
            "no approval": {"none": "no app"},
            "placeholder approval": {"none": "no app", "approval": "<approval reference>"},
            "empty reason": {"none": "", "approval": "ADR-0001"},
            "extra key": {"none": "no app", "approval": "ADR-0001", "until": "later"},
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                profile = complete_profile()
                profile["verification_surface"] = value
                self.assertEqual(
                    keys_of(profile_module.validate_profile(profile)), {"verification_surface"}
                )
        profile = complete_profile()
        profile["run_events"]["nightly_scheduler"] = "none: no scheduler host"
        self.assertEqual(
            keys_of(profile_module.validate_profile(profile)), {"run_events.nightly_scheduler"}
        )

    def test_budget_limits_and_floors_must_be_real_numbers(self) -> None:
        cases = {
            ("fast_tier_budget", "machines", 0, "wall_limit_s"): ["20", 0, -1, True],
            ("coverage", "line_floor"): [101, -1, "85"],
        }
        for path, values in cases.items():
            for value in values:
                with self.subTest(path=path, value=value):
                    profile = complete_profile()
                    target = profile
                    for step in path[:-1]:
                        target = target[step]
                    target[path[-1]] = value
                    self.assertEqual(len(profile_module.validate_profile(profile)), 1)

    def test_unknown_keys_and_house_style_version_drift_are_reported(self) -> None:
        profile = complete_profile()
        profile["teirs"] = {}
        profile["guards"]["proces"] = "x"
        profile["house_style"]["version"] = 99
        self.assertEqual(
            keys_of(profile_module.validate_profile(profile)),
            {"teirs", "guards.proces", "house_style.version"},
        )

    def test_house_style_exceptions_need_an_adr(self) -> None:
        profile = complete_profile()
        profile["house_style"]["exceptions"] = [{"rule": "NAME.conventions", "path": "src/x"}]
        self.assertEqual(
            keys_of(profile_module.validate_profile(profile)), {"house_style.exceptions[0].adr"}
        )

    def test_checker_settings_may_hold_regular_expressions(self) -> None:
        profile = complete_profile()
        profile["guardrails"]["naming"]["pattern"] = r"(?P<name>[a-z]+)"
        self.assertEqual(profile_module.validate_profile(profile), [])


class FindingsTests(unittest.TestCase):
    def test_findings_render_sorted_unique_single_lines(self) -> None:
        later = Finding("B.rule", "b.py", "two\n words", "fix b")
        earlier = Finding("A.rule", "a.py", "one", "fix a")
        self.assertEqual(
            format_findings([later, earlier, later]),
            "a.py: A.rule: one; fix a\nb.py: B.rule: two words; fix b",
        )
        self.assertEqual((exit_code([]), exit_code([earlier])), (0, 1))


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-cli-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def write_profile(self, profile: dict) -> None:
        path = self.repo / ".omp/test-profile.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\n{json.dumps(profile, indent=2)}\n---\n\n# Repo test profile\n")

    def test_every_contract_subcommand_is_listed(self) -> None:
        self.assertEqual(set(cli.SUBCOMMANDS), CONTRACT_SUBCOMMANDS)
        code, out, _ = self.run_cli("--help")
        self.assertEqual(code, 0)
        for name in CONTRACT_SUBCOMMANDS:
            self.assertIn(name, out)

    def test_subcommand_help_needs_no_profile_or_pytest_extra(self) -> None:
        # The child blocks pytest and ast_grep_py imports, as an install without the extra would.
        entry = (
            "import sys; sys.modules['pytest'] = None; sys.modules['ast_grep_py'] = None; "
            "from omp_guardrails import cli; raise SystemExit(cli.main(sys.argv[1:]))"
        )
        for name in sorted(CONTRACT_SUBCOMMANDS - {"adapter"}):
            with self.subTest(subcommand=name):
                result = subprocess.run(
                    [sys.executable, "-c", entry, name, "--repo", str(self.repo), "--help"],
                    cwd=REFERENCE,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(name, result.stdout)
                self.assertNotIn("does not exist", result.stdout)

    def test_checker_modules_are_imported_only_when_their_subcommand_runs(self) -> None:
        probe = (
            "import sys; from omp_guardrails import cli; "
            "listed = {m for item in cli.SUBCOMMANDS.values() for m in item.modules}; "
            "print(sorted(listed & set(sys.modules) - {'omp_guardrails.config.profile'}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], cwd=REFERENCE, capture_output=True, text=True, check=True
        )
        self.assertEqual(result.stdout.strip(), "[]")

    def test_profile_check_exit_codes_follow_findings(self) -> None:
        self.write_profile(complete_profile())
        self.assertEqual(
            self.run_cli("profile-check", "--repo", str(self.repo))[:2], (0, "profile-check: ok\n")
        )
        broken = complete_profile()
        broken["adapter"] = "TBD"
        self.write_profile(broken)
        code, out, _ = self.run_cli("profile-check", f"--repo={self.repo}")
        self.assertEqual(code, 1)
        self.assertIn(".omp/test-profile.md: PROFILE.complete: adapter is missing", out)

    def test_missing_profile_is_a_profile_finding(self) -> None:
        code, out, _ = self.run_cli("naming", "--repo", str(self.repo))
        self.assertEqual(code, 1)
        self.assertIn("PROFILE.complete: .omp/test-profile.md does not exist", out)

    def test_unknown_subcommand_and_missing_adapter_are_usage_errors(self) -> None:
        self.assertEqual(self.run_cli("lint-everything")[0], 2)
        profile = copy.deepcopy(complete_profile())
        profile["adapter"] = "framework_without_adapter"
        self.write_profile(profile)
        code, _, err = self.run_cli("adapter", "--repo", str(self.repo))
        self.assertEqual(code, 2)
        self.assertIn("cannot import omp_guardrails.adapters", err)


if __name__ == "__main__":
    unittest.main()
