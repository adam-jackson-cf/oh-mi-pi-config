"""Repo config completeness (CONFIG.complete): every required config value present and approved."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import approval, exception, fixture

from guardrails.commands.init import sections_draft
from guardrails.context.config import section_schema, validator

pytestmark = pytest.mark.fast


def problems(config: dict[str, Any]) -> list[str]:
    return validator.validate(config, origin=None)


def keys_of(found: list[str]) -> set[str]:
    return {problem.split(": ", 1)[0] for problem in found}


def set_nested(target: Any, path: tuple[object, ...], value: object) -> None:
    for step in path[:-1]:
        target = target[step]
    target[path[-1]] = value


def exempt(config: dict[str, Any], target: str) -> None:
    """Mark tests.<target> {none: true} and record the owner's exemption approval."""
    parent, _, leaf = target.rpartition(".")
    holder = config["tests"][parent] if parent else config["tests"]
    holder[leaf] = {"none": True}
    config["approvals"].append(approval("exemption", target, "owner-approved reason"))


class ConfigCompleteTests(unittest.TestCase):
    def test_complete_config_has_no_problems(self) -> None:
        assert problems(fixture()) == []

    def test_section_template_reports_every_setup_section(self) -> None:
        found = sections_draft.section_problems(sections_draft.template())
        assert {key.split(".")[0] for key in keys_of(found)} == {
            "commands",
            "structure",
            "houseStyle",
            "tests",
        }
        reported = {key.split(".")[1].split("[")[0] for key in keys_of(found)}
        assert set(validator.TEST_KEYS) <= reported

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
                config = fixture()
                config["tests"]["run_events"]["commit"] = value
                assert keys_of(problems(config)) == {"tests.run_events.commit"}

    def test_absent_keys_and_empty_lists_count_as_missing(self) -> None:
        config = fixture()
        del config["tests"]["guards"]["network"]
        config["tests"]["fast_tier_budget"]["machines"] = []
        config["tests"]["layout"]["test_file_patterns"] = []
        assert keys_of(problems(config)) == {
            "tests.guards.network",
            "tests.fast_tier_budget.machines",
            "tests.layout.test_file_patterns",
            "approvals[0].target",
        }

    def test_nested_nulls_in_free_form_values_count_as_missing(self) -> None:
        cases = {
            "tests.public_entry_points": {"ledger": None},
            "tests.identity_artefacts": {"docs/ids.json": None},
            "structure.owners": {"directories": {"src/ledger/": None}},
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                config = fixture()
                section, leaf = key.split(".")
                config[section][leaf] = value
                assert keys_of(problems(config)) == {key}

    def test_command_notes_need_both_owner_notes_and_allow_the_lsp_note(self) -> None:
        cases = {
            "text instead of the notes object": ("repo root", {"tests.command_notes"}),
            "missing interpretation": (
                {"working_directory": "repo root"},
                {"tests.command_notes.interpretation"},
            ),
            "placeholder working directory": (
                {"working_directory": "<directory>", "interpretation": "exit 0 passes"},
                {"tests.command_notes.working_directory"},
            ),
            "null lsp note": (
                {
                    "working_directory": "repo root",
                    "interpretation": "exit 0",
                    "lsp": None,
                },
                {"tests.command_notes.lsp"},
            ),
        }
        for name, (value, expected) in cases.items():
            with self.subTest(name=name):
                config = fixture()
                config["tests"]["command_notes"] = value
                assert keys_of(problems(config)) == expected
        config = fixture()
        config["tests"]["command_notes"]["lsp"] = [
            {"server": "pyright", "install": "npm i -g pyright"}
        ]
        assert problems(config) == []

    def test_approved_none_is_allowed_only_in_the_exemptable_places(self) -> None:
        config = fixture()
        for target in (
            "verification_surface",
            "identity_artefacts",
            "run_events.nightly_scheduler",
        ):
            exempt(config, target)
        assert problems(config) == []
        for key in ("coverage", "guards", "environments", "layout"):
            with self.subTest(key=key):
                elsewhere = fixture()
                elsewhere["tests"][key] = {"none": True}
                assert f"tests.{key}" in {
                    ".".join(k.split(".")[:2]) for k in keys_of(problems(elsewhere))
                }

    def test_an_approved_t3_exemption_replaces_the_t3_tier_and_its_adapters(
        self,
    ) -> None:
        config = fixture()
        exempt(config, "tiers.t3")
        config["tests"]["adapters"][0]["tiers"].pop("t3")
        assert problems(config) == []
        assert section_schema.exempt_tiers(config) == {"t3"}

    def test_a_t3_exemption_needs_an_approval_and_no_adapter_in_t3(self) -> None:
        config = fixture()
        t3_selection = config["tests"]["adapters"][0]["tiers"].pop("t3")
        config["tests"]["tiers"]["t3"] = {"none": True}
        assert keys_of(problems(config)) == {"tests.tiers.t3"}
        assert section_schema.exempt_tiers(config) == set()
        config["approvals"].append(approval("exemption", "tiers.t3", "no suites yet"))
        assert problems(config) == []
        config["tests"]["adapters"][0]["tiers"]["t3"] = t3_selection
        assert keys_of(problems(config)) == {"tests.adapters[0].tiers.t3"}

    def test_only_t3_may_be_exempted_among_the_tiers(self) -> None:
        for tier in ("t1", "t2"):
            with self.subTest(tier=tier):
                config = fixture()
                config["tests"]["tiers"][tier] = {"none": True}
                assert f"tests.tiers.{tier}.command" in keys_of(problems(config))
                assert section_schema.exempt_tiers(config) == set()

    def test_unapproved_or_malformed_exemptions_count_as_missing(self) -> None:
        cases = {
            "reason text instead of true": (
                {"none": "no app"},
                "tests.verification_surface.none",
            ),
            "approval text inside": (
                {"none": True, "approval": "ADR-0001"},
                "tests.verification_surface.approval",
            ),
            "no exemption approval": ({"none": True}, "tests.verification_surface"),
        }
        for name, (value, key) in cases.items():
            with self.subTest(name=name):
                config = fixture()
                config["tests"]["verification_surface"] = value
                assert key in keys_of(problems(config))
        config = fixture()
        config["tests"]["run_events"]["nightly_scheduler"] = "none: no scheduler host"
        assert keys_of(problems(config)) == {"tests.run_events.nightly_scheduler"}

    def test_budget_limits_and_floors_must_be_real_numbers(self) -> None:
        cases = {
            ("tests", "fast_tier_budget", "machines", 0, "wall_limit_s"): [
                "20",
                0,
                -1,
                True,
            ],
            ("tests", "coverage", "line_floor"): [101, -1, "85"],
        }
        for path, values in cases.items():
            for value in values:
                with self.subTest(path=path, value=value):
                    config = fixture()
                    set_nested(config, path, value)
                    assert len(problems(config)) == 1

    def test_unknown_keys_and_house_style_version_drift_are_reported(self) -> None:
        config = fixture()
        config["teirs"] = {}
        config["tests"]["guards"]["proces"] = "x"
        config["houseStyle"]["version"] = 99
        assert keys_of(problems(config)) == {
            "teirs",
            "tests.guards.proces",
            "houseStyle.version",
        }

    def test_checker_sections_must_name_a_checker_and_hold_an_object(self) -> None:
        config = fixture()
        config["checkers"]["namig"] = {"pattern": "x"}
        config["checkers"]["python"] = ["src"]
        assert keys_of(problems(config)) == {"checkers.namig", "checkers.python"}

    def test_checker_settings_may_hold_regular_expressions(self) -> None:
        config = fixture()
        config["checkers"]["naming"] = {"pattern": r"(?P<name>[a-z]+)"}
        assert problems(config) == []

    def test_exceptions_require_real_strings_and_reject_unknown_fields(self) -> None:
        entry = exception(
            "NAME.conventions",
            "internal/**/*_test.go",
            "Go tests retain the package naming convention",
            check="child-repeats-parent",
        )
        for field in ("rule", "check", "path", "reason"):
            for value in ("", None, 42):
                with self.subTest(field=field, value=value):
                    config = fixture()
                    config["exceptions"] = [{**entry, field: value}]
                    assert f"exceptions[0].{field}" in keys_of(problems(config))
        for field in ("rule", "path", "reason", "approvedBy", "approvedAt"):
            with self.subTest(missing=field):
                config = fixture()
                config["exceptions"] = [
                    {key: value for key, value in entry.items() if key != field}
                ]
                assert keys_of(problems(config)) == {f"exceptions[0].{field}"}
        config = fixture()
        config["exceptions"] = [{**entry, "unexpected": "old decision reference"}]
        assert keys_of(problems(config)) == {"exceptions[0].unexpected"}

    def test_exceptions_accept_globs_optional_checks_and_plain_reasons(self) -> None:
        for entries in (
            [],
            [exception("NAME.conventions", "src/**", "Generated API names")],
            [
                exception(
                    "NAME.conventions",
                    "internal/**/*_test.go",
                    "Go package naming convention",
                    check="child-repeats-parent",
                )
            ],
        ):
            with self.subTest(entries=entries):
                config = fixture()
                config["exceptions"] = entries
                assert problems(config) == []


if __name__ == "__main__":
    unittest.main()
