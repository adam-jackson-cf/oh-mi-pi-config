"""Record profile loading, PROFILE.complete, findings and CLI dispatch of the guardrails reference."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands import cli
from guardrails.context.config import profile as profile_module
from guardrails.foundation.findings import Finding, exit_code, format_findings
from guardrails.toolkit import dispatch

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = ROOT / "agent/lib/record/profile.md"

FIXTURES = ROOT / "tests/fixtures/reference/core"

CONTRACT_SUBCOMMANDS = {
    "check",
    "profile-check",
    "init",
    "path-check",
    "structure",
    "rename-residue",
    "prefix-candidates",
    "structure-state",
    "naming",
    "python-quality",
    "python-fanout",
    "python-scalability",
    "python-test-hygiene",
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
}


def complete_profile() -> dict[str, Any]:
    return profile_module.parse_front_matter(
        (FIXTURES / "complete-profile.md").read_text()
    )


def keys_of(findings: list[Finding]) -> set[str]:
    return {finding.message.split(" ", 1)[0] for finding in findings}


def set_nested(target: Any, path: tuple[object, ...], value: object) -> None:
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value


class ProfileLoadingTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-core-")
        self.addCleanup(scratch.cleanup)
        self.record = Path(scratch.name)

    def write_profile(self, text: str) -> None:
        (self.record / "profile.md").write_text(text)

    def test_front_matter_json_is_returned_as_a_dict(self) -> None:
        self.write_profile((FIXTURES / "complete-profile.md").read_text())
        assert (
            profile_module.load_profile(self.record)["adapters"][0]["name"] == "pytest"
        )

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
                with pytest.raises(profile_module.ProfileError):
                    profile_module.load_profile(self.record)


class ProfileCompleteTests(unittest.TestCase):
    def test_complete_profile_has_no_findings(self) -> None:
        assert profile_module.validate_profile(complete_profile()) == []

    def test_template_front_matter_parses_and_reports_every_section(self) -> None:
        template = profile_module.parse_front_matter(TEMPLATE.read_text())
        required = set(profile_module.SCHEMA)
        assert set(template) - {"guardrails"} == required
        findings = profile_module.validate_profile(template)
        reported = {key.split(".")[0].split("[")[0] for key in keys_of(findings)}
        assert reported == required | {"guardrails"}
        assert all(item.rule_id == "PROFILE.complete" for item in findings)

    def test_placeholders_and_sentinels_count_as_missing(self) -> None:
        for value in (
            "<command>",
            "n/a",
            "N/A",
            "TBD",
            "run TBD later",
            "",
            "none",
            "None: later",
        ):
            with self.subTest(value=value):
                profile = complete_profile()
                profile["run_events"]["commit"] = value
                assert keys_of(profile_module.validate_profile(profile)) == {
                    "run_events.commit"
                }

    def test_absent_keys_and_empty_lists_count_as_missing(self) -> None:
        profile = complete_profile()
        del profile["guards"]["network"]
        profile["fast_tier_budget"]["machines"] = []
        profile["layout"]["test_file_patterns"] = []
        assert keys_of(profile_module.validate_profile(profile)) == {
            "guards.network",
            "fast_tier_budget.machines",
            "layout.test_file_patterns",
        }

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
                assert keys_of(profile_module.validate_profile(profile)) == {key}

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
                {
                    "working_directory": "repo root",
                    "interpretation": "exit 0",
                    "lsp": None,
                },
                {"command_notes.lsp"},
            ),
        }
        for name, (value, expected) in cases.items():
            with self.subTest(name=name):
                profile = complete_profile()
                profile["command_notes"] = value
                assert keys_of(profile_module.validate_profile(profile)) == expected
        profile = complete_profile()
        profile["command_notes"]["lsp"] = [
            {"server": "pyright", "install": "npm i -g pyright"}
        ]
        assert profile_module.validate_profile(profile) == []

    def test_approved_none_is_allowed_only_in_three_places(self) -> None:
        exemption = {"none": "owner-approved reason", "approval": "ADR-0001"}
        profile = complete_profile()
        profile["verification_surface"] = dict(exemption)
        profile["identity_artefacts"] = dict(exemption)
        profile["run_events"]["nightly_scheduler"] = dict(exemption)
        assert profile_module.validate_profile(profile) == []
        for key in ("coverage", "guards", "environments", "layout"):
            with self.subTest(key=key):
                elsewhere = complete_profile()
                elsewhere[key] = dict(exemption)
                assert key in {
                    k.split(".")[0]
                    for k in keys_of(profile_module.validate_profile(elsewhere))
                }

    def test_an_approved_t3_exemption_replaces_the_t3_tier_and_its_adapters(
        self,
    ) -> None:
        exemption = {"none": "no exploratory suites yet", "approval": "ADR-0009"}
        profile = complete_profile()
        profile["tiers"]["t3"] = dict(exemption)
        profile["adapters"][0]["tiers"].pop("t3")
        assert profile_module.validate_profile(profile) == []
        assert profile_module.exempt_tiers(profile) == {"t3"}

    def test_a_t3_exemption_needs_an_approval_and_no_adapter_in_t3(self) -> None:
        profile = complete_profile()
        t3_selection = profile["adapters"][0]["tiers"].pop("t3")
        profile["tiers"]["t3"] = {"none": "no exploratory suites yet"}
        assert keys_of(profile_module.validate_profile(profile)) == {"tiers.t3"}
        profile["tiers"]["t3"]["approval"] = "ADR-0009"
        assert profile_module.validate_profile(profile) == []
        profile["adapters"][0]["tiers"]["t3"] = t3_selection
        assert keys_of(profile_module.validate_profile(profile)) == {
            "adapters[0].tiers.t3"
        }

    def test_only_t3_may_be_exempted_among_the_tiers(self) -> None:
        exemption = {"none": "no unit tests", "approval": "ADR-0009"}
        for tier in ("t1", "t2"):
            with self.subTest(tier=tier):
                profile = complete_profile()
                profile["tiers"][tier] = dict(exemption)
                assert f"tiers.{tier}.command" in keys_of(
                    profile_module.validate_profile(profile)
                )
                assert profile_module.exempt_tiers(profile) == set()

    def test_unapproved_or_malformed_exemptions_count_as_missing(self) -> None:
        cases = {
            "no approval": {"none": "no app"},
            "placeholder approval": {
                "none": "no app",
                "approval": "<approval reference>",
            },
            "empty reason": {"none": "", "approval": "ADR-0001"},
            "extra key": {"none": "no app", "approval": "ADR-0001", "until": "later"},
        }
        for name, value in cases.items():
            with self.subTest(name=name):
                profile = complete_profile()
                profile["verification_surface"] = value
                assert keys_of(profile_module.validate_profile(profile)) == {
                    "verification_surface"
                }
        profile = complete_profile()
        profile["run_events"]["nightly_scheduler"] = "none: no scheduler host"
        assert keys_of(profile_module.validate_profile(profile)) == {
            "run_events.nightly_scheduler"
        }

    def test_budget_limits_and_floors_must_be_real_numbers(self) -> None:
        cases = {
            ("fast_tier_budget", "machines", 0, "wall_limit_s"): ["20", 0, -1, True],
            ("coverage", "line_floor"): [101, -1, "85"],
        }
        for path, values in cases.items():
            for value in values:
                with self.subTest(path=path, value=value):
                    profile = complete_profile()
                    set_nested(profile, path, value)
                    assert len(profile_module.validate_profile(profile)) == 1

    def test_unknown_keys_and_house_style_version_drift_are_reported(self) -> None:
        profile = complete_profile()
        profile["teirs"] = {}
        profile["guards"]["proces"] = "x"
        profile["house_style"]["version"] = 99
        assert keys_of(profile_module.validate_profile(profile)) == {
            "teirs",
            "guards.proces",
            "house_style.version",
        }

    def test_guardrails_sections_must_name_a_checker_and_hold_an_object(self) -> None:
        profile = complete_profile()
        profile["guardrails"]["namig"] = {"pattern": "x"}
        profile["guardrails"]["python"] = ["src"]
        assert keys_of(profile_module.validate_profile(profile)) == {
            "guardrails.namig",
            "guardrails.python",
        }

    def test_checker_settings_may_hold_regular_expressions(self) -> None:
        profile = complete_profile()
        profile["guardrails"]["naming"]["pattern"] = r"(?P<name>[a-z]+)"
        assert profile_module.validate_profile(profile) == []


class FindingsTests(unittest.TestCase):
    def test_findings_render_sorted_unique_single_lines(self) -> None:
        later = Finding("B.rule", "b.py", "two\n words", "fix b")
        earlier = Finding("A.rule", "a.py", "one", "fix a")
        assert (
            format_findings([later, earlier, later])
            == "a.py: A.rule: one; fix a\nb.py: B.rule: two words; fix b"
        )
        assert (exit_code([]), exit_code([earlier])) == (0, 1)


class CliCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-cli-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        self.record = Path(scratch.name).resolve() / "record"
        self.repo.mkdir()
        self.record.mkdir()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def write_profile(self, profile: dict[str, Any]) -> None:
        path = self.record / "profile.md"
        path.write_text(
            f"---\n{json.dumps(profile, indent=2)}\n---\n\n# Repo test profile\n"
        )

    def located(self, *args: str) -> tuple[str, ...]:
        return (
            *args[:1],
            "--repo",
            str(self.repo),
            "--record",
            str(self.record),
            *args[1:],
        )


class CliTests(CliCase):
    def test_every_contract_subcommand_is_listed(self) -> None:
        assert set(dispatch.SUBCOMMANDS) == CONTRACT_SUBCOMMANDS
        code, out, _ = self.run_cli("--help")
        assert code == 0
        for name in CONTRACT_SUBCOMMANDS:
            assert name in out

    def test_profile_check_exit_codes_follow_findings(self) -> None:
        self.write_profile(complete_profile())
        assert self.run_cli(*self.located("profile-check"))[:2] == (
            0,
            "profile-check: ok\n",
        )
        broken = complete_profile()
        broken["adapters"][0]["name"] = "TBD"
        self.write_profile(broken)
        code, out, _ = self.run_cli(
            "profile-check", f"--record={self.record}", f"--repo={self.repo}"
        )
        assert code == 1
        assert "record:profile.md: PROFILE.complete: adapters[0].name is missing" in out

    def test_exception_records_reject_obsolete_location_with_migration_error(
        self,
    ) -> None:
        profile = complete_profile()
        profile["house_" + "style"]["exceptions"] = []
        self.write_profile(profile)
        code, out, _ = self.run_cli(*self.located("profile-check"))
        assert code == 1
        assert "is obsolete; move entries to guardrails.exceptions" in out
        assert "reason" in out

    def test_exception_records_require_real_strings_and_reject_unknown_fields(
        self,
    ) -> None:
        entry = {
            "rule": "NAME.conventions",
            "check": "child-repeats-parent",
            "path": "internal/**/*_test.go",
            "reason": "Go tests retain the package naming convention",
        }
        for field in ("rule", "check", "path", "reason"):
            for value in ("", "TBD", "<value>", None, 42):
                with self.subTest(field=field, value=value):
                    profile = complete_profile()
                    profile["guardrails"]["exceptions"] = [{**entry, field: value}]
                    self.write_profile(profile)
                    code, out, _ = self.run_cli(*self.located("profile-check"))
                    assert code == 1
                    assert f"guardrails.exceptions[0].{field}" in out
        for field in ("rule", "path", "reason"):
            with self.subTest(missing=field):
                profile = complete_profile()
                profile["guardrails"]["exceptions"] = [
                    {key: value for key, value in entry.items() if key != field}
                ]
                self.write_profile(profile)
                code, out, _ = self.run_cli(*self.located("profile-check"))
                assert code == 1
                assert f"guardrails.exceptions[0].{field}" in out
        profile = complete_profile()
        for field in ("unexpected", "a" + "dr"):
            with self.subTest(unknown=field):
                profile["guardrails"]["exceptions"] = [
                    {**entry, field: "old decision reference"}
                ]
                self.write_profile(profile)
                code, out, _ = self.run_cli(*self.located("profile-check"))
                assert code == 1
                assert f"guardrails.exceptions[0].{field} is not a profile key" in out

    def test_exception_records_accept_globs_optional_checks_and_plain_reasons(
        self,
    ) -> None:
        for entries in (
            [],
            [
                {
                    "rule": "NAME.conventions",
                    "path": "src/**",
                    "reason": "Generated API names",
                }
            ],
            [
                {
                    "rule": "NAME.conventions",
                    "check": "child-repeats-parent",
                    "path": "internal/**/*_test.go",
                    "reason": "Go package naming convention",
                }
            ],
        ):
            with self.subTest(entries=entries):
                profile = complete_profile()
                profile["guardrails"]["exceptions"] = entries
                self.write_profile(profile)
                assert self.run_cli(*self.located("profile-check"))[:2] == (
                    0,
                    "profile-check: ok\n",
                )

    def test_missing_profile_is_a_profile_finding(self) -> None:
        code, out, _ = self.run_cli(*self.located("naming"))
        assert code == 1
        assert "PROFILE.complete: record:profile.md does not exist" in out

    def test_every_checker_needs_a_record_outside_the_repository(self) -> None:
        self.write_profile(complete_profile())
        code, _, err = self.run_cli("naming", "--repo", str(self.repo))
        assert code == 2
        assert "pass --record DIR" in err
        inside = self.repo / "record"
        inside.mkdir()
        code, _, err = self.run_cli(
            "naming", "--repo", str(self.repo), "--record", str(inside)
        )
        assert code == 2
        assert "inside the repository" in err
        assert self.run_cli("naming", "--repo", str(self.repo), "--record")[0] == 2

    def test_unknown_subcommand_and_missing_adapter_are_usage_errors(self) -> None:
        assert self.run_cli("lint-everything")[0] == 2
        profile = copy.deepcopy(complete_profile())
        profile["adapters"][0]["name"] = "framework_without_adapter"
        self.write_profile(profile)
        code, _, err = self.run_cli(
            *self.located("adapter"), "framework_without_adapter", "lint"
        )
        assert code == 2
        assert "cannot import guardrails.commands.adapters" in err

    def test_adapter_takes_a_declared_adapter_name_first(self) -> None:
        self.write_profile(complete_profile())
        for argv in ([], ["vitest", "lint"], ["lint"]):
            with self.subTest(argv=argv):
                code, out, _ = self.run_cli(*self.located("adapter"), *argv)
                assert code == 1
                assert "adapter takes one of the profile's adapters ['pytest']" in out


if __name__ == "__main__":
    unittest.main()
