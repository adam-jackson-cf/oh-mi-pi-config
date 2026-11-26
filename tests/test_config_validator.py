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
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest

from guardrails.commands import cli
from guardrails.commands.check import whole_repo
from guardrails.context.config import entry_points, section_schema, validator
from guardrails.context.config.validator import ConfigError, OfferedQuestion
from guardrails.foundation import decision_store

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/agentrails"
EXPECTED: dict[str, list[str]] = json.loads(
    (FIXTURES / "expected-problems.json").read_text()
)


def fixture(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((FIXTURES / "valid" / name).read_text())
    return loaded


def outcome(path: Path) -> tuple[str, list[str]]:
    try:
        config = validator.read_config(path)
    except ConfigError as error:
        return error.kind, error.problems
    return "invalid", validator.validate(config, origin=None)


def run_cli(*args: str) -> tuple[int, dict[str, Any]]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(["config-check", *args])
    return code, json.loads(out.getvalue())


class Fixtures(unittest.TestCase):
    def test_every_valid_fixture_validates_against_its_own_slug(self) -> None:
        names = sorted(path.name for path in (FIXTURES / "valid").glob("*.json"))
        assert names == [
            "ci-disabled.json",
            "full.json",
            "judgment-overrides.json",
            "minimal.json",
        ]
        for name in names:
            with self.subTest(fixture=name):
                config = fixture(name)
                origin = config["repo"]["slug"]
                assert validator.validate(config, origin=origin) == []

    def test_every_invalid_fixture_fails_with_exactly_its_named_problem(self) -> None:
        names = sorted(path.name for path in (FIXTURES / "invalid").glob("*.json"))
        assert names == sorted(EXPECTED)
        for name in names:
            with self.subTest(fixture=name):
                kind, problems = outcome(FIXTURES / "invalid" / name)
                assert kind == "invalid"
                assert len(problems) == len(EXPECTED[name]), problems
                for problem, expected in zip(problems, EXPECTED[name], strict=True):
                    assert problem.startswith(expected), problem


class Validation(unittest.TestCase):
    def test_a_slug_that_differs_from_the_origin_is_refused(self) -> None:
        config = fixture("minimal.json")
        assert validator.validate(config, origin="example-owner/other") == [
            "repo.slug: is 'example-owner/ledger' but the origin remote names "
            "'example-owner/other'"
        ]
        assert validator.validate(config, origin=None) == []

    def test_a_config_that_is_not_an_object_is_refused(self) -> None:
        assert validator.validate([], origin=None) == ["config: must be a JSON object"]

    def test_shadow_is_refused_for_a_question_without_a_non_blocking_outcome(
        self,
    ) -> None:
        setting = {"enabled": True, "mode": "shadow"}
        assert validator._mode_problem(
            OfferedQuestion("enforced", shadowable=False), setting
        ) == (
            "cannot run in shadow mode: only a bool answer or a choice option "
            "threshold has a non-blocking outcome"
        )
        assert (
            validator._mode_problem(OfferedQuestion("enforced", True), setting) is None
        )
        assert (
            validator._mode_problem(OfferedQuestion("shadow", True), {"enabled": False})
            is None
        )

    def test_a_malformed_judgment_setting_reports_its_shape(self) -> None:
        config = fixture("minimal.json")
        config["judgments"] = {
            "extension-impact/impact": {"enabled": True},
            "completion-evidence/evidence_gap": {"enabled": "no"},
        }
        assert validator.validate(config, origin=None) == [
            "judgments.extension-impact/impact.mode: is missing",
            "judgments.completion-evidence/evidence_gap.enabled: must be one of [false, true]",
        ]

    def test_scalar_sections_report_their_shape(self) -> None:
        config = fixture("minimal.json")
        for name in ("judgments", "tests", "approvals", "exceptions", "repo"):
            config[name] = "none"
        config["gates"]["rules"] = []
        config["gates"]["prove"] = "yes"
        config["ci"] = []
        assert validator.validate(config, origin=None) == [
            "repo: must be an object",
            "ci: must be an object",
            "gates.prove: must be true or false",
            "gates.rules: must be an object",
            "judgments: must be an object",
            "tests: must be an object",
            "exceptions: must be a list",
            "approvals: must be a list",
        ]

    def test_leaf_shapes_name_the_expected_value(self) -> None:
        config = fixture("full.json")
        config["onboarding"]["questionnaireVersion"] = True
        config["onboarding"]["skills"]["test-guidance"] = {
            "version": "ABC",
            "ranAt": "2026-13-40T99:00:00Z",
            "scope": {"kind": "paths", "paths": [" "]},
        }
        config["afterMerge"] = {"argv": ["make"], "paths": []}
        config["mergePolicies"][0]["headPrefix"] = 3
        config["checkers"]["check"]["run"] = []
        config["checkers"]["naming"] = []
        config["exceptions"][0]["approvedBy"] = " "
        config["exceptions"].append("NAME.conventions")
        assert validator.validate(config, origin=None) == [
            "onboarding.questionnaireVersion: must be an integer of at least 1",
            "onboarding.skills.test-guidance.version: must be a lowercase hex sha256 digest",
            "onboarding.skills.test-guidance.ranAt: must be an ISO 8601 date-time with a "
            "UTC offset, e.g. 2026-11-24T09:00:00Z",
            "onboarding.skills.test-guidance.scope.paths: must be a non-empty list of "
            "non-blank strings",
            "mergePolicies[0].headPrefix: must be a string",
            "afterMerge.paths: must be a non-empty list of non-blank strings",
            "checkers.naming: must be an object",
            "checkers.check.run: must be a non-empty list of steps from "
            f"{list(validator.CHECK_STEPS)}",
            "exceptions[0].approvedBy: must be a non-blank string",
            "exceptions[1]: must be an object",
        ]

    def test_an_unknown_skill_scope_names_its_options(self) -> None:
        config = fixture("minimal.json")
        config["onboarding"]["skills"]["setup-repo-proofs"]["scope"] = {"kind": "all"}
        config["autonomy"] = []
        assert validator.validate(config, origin=None) == [
            "onboarding.skills.setup-repo-proofs.scope.kind: must be one of "
            '["whole", "changed", "paths"]',
            "autonomy: must be an object",
        ]

    def test_an_exemption_approval_must_name_an_exemptable_field(self) -> None:
        config = fixture("minimal.json")
        config["approvals"].append({
            **config["approvals"][0],
            "kind": "exemption",
            "target": "coverage",
        })
        assert validator.validate(config, origin=None) == [
            "approvals[1].target: must name an exemptable field "
            f"{list(section_schema.EXEMPTION_TARGETS)}"
        ]

    def test_tests_reuse_the_section_schema_adapter_rules(self) -> None:
        config = fixture("minimal.json")
        config["tests"]["adapters"][0]["name"] = "nose"
        assert validator.validate(config, origin=None) == [
            "tests.adapters[0].name: 'nose' is not an installed adapter package"
        ]


class Sections(unittest.TestCase):
    def test_each_checked_section_of_the_full_fixture_validates_alone(self) -> None:
        config = fixture("full.json")
        for name in validator.CHECKED_SECTIONS:
            with self.subTest(section=name):
                assert validator.validate_section(name, config[name]) == []

    def test_a_section_reports_problems_under_its_own_name(self) -> None:
        tests = copy.deepcopy(fixture("minimal.json")["tests"])
        tests["fast_tier_budget"]["machines"][0]["approval"] = "ADR-0008"
        assert validator.validate_section("tests", tests) == [
            "tests.fast_tier_budget.machines[0].approval: is not a config key; remove "
            "it or fix its spelling"
        ]
        assert validator.validate_section("tests", []) == ["tests: must be an object"]

    def test_sections_outside_the_checked_set_are_refused(self) -> None:
        with pytest.raises(ValueError, match="'repo' is not a section"):
            validator.validate_section("repo", {})

    def test_owner_sections_check_their_entries_alone(self) -> None:
        approval = fixture("minimal.json")["approvals"][0]
        assert validator.validate_section("approvals", [approval]) == []
        assert validator.validate_section("approvals", [approval, approval]) == [
            "approvals[1]: repeats an earlier (kind, target)"
        ]
        exception = {
            "rule": "NOT.a-rule",
            "path": "src/**",
            "reason": "r",
            "approvedBy": "o",
            "approvedAt": "2026-11-24T09:00:00Z",
        }
        assert validator.validate_section("exceptions", [exception]) == [
            'exceptions[0].rule: "NOT.a-rule" is not a rule id in the guardrail '
            "catalogue or a judgment question"
        ]


class EntryPointTests(unittest.TestCase):
    def test_each_owned_package_maps_to_a_list_of_modules_and_symbols(self) -> None:
        config = {
            "tests": {
                "public_entry_points": {
                    "ledger": ["ledger.cli:main", "ledger.adapters.bank:run"],
                    "web/src": ["web/src/index.ts:App"],
                    "cart": ["example.com/shop/cart:Total"],
                }
            }
        }
        assert entry_points.entry_points(config) == (
            entry_points.EntryPoint("ledger", "ledger.cli", "main"),
            entry_points.EntryPoint("ledger", "ledger.adapters.bank", "run"),
            entry_points.EntryPoint("web/src", "web/src/index.ts", "App"),
            entry_points.EntryPoint("cart", "example.com/shop/cart", "Total"),
        )
        assert entry_points.packages(config) == ("ledger", "web/src", "cart")
        assert entry_points.entry_points({}) == ()
        assert entry_points.entry_points({"tests": {"public_entry_points": {}}}) == ()

    def test_every_other_form_is_a_config_error(self) -> None:
        for value in [
            {"shop": ["checkout"]},
            {"shop": "shop.cli:main"},
            {"shop": []},
            {"shop": ["shop.cli:main", 3]},
            {"shop": "shop.cli"},
            {"shop": {".": "./src/index.ts"}},
            {"shop": "shop.cli:main.run"},
            {"<package>": "<public entry point>"},
            ["shop.cli:main"],
        ]:
            with self.subTest(value=value):
                config = {"tests": {"public_entry_points": value}}
                with pytest.raises(section_schema.ConfigValueError):
                    entry_points.entry_points(config)
                assert section_schema.leaf_problem(value, "entry_points") is not None


class Constants(unittest.TestCase):
    def test_sections_and_targets_match_the_engine_tables(self) -> None:
        assert len(validator.SECTIONS) == 16
        assert set(validator.CHECKED_SECTIONS) < set(validator.SECTIONS)
        assert set(section_schema.EXEMPTION_TARGETS) == {
            "tiers.t3",
            "verification_surface",
            "identity_artefacts",
            "run_events.nightly_scheduler",
        }
        assert whole_repo.DEFAULT_STEPS == (
            "python-quality",
            "python-fanout",
            "python-scalability",
            "surface-lint",
            "adapter lint",
        )
        assert validator.GATE_KEYS[-1] == "rules"

    def test_code_check_rules_leave_out_process_rules(self) -> None:
        rules = validator.code_check_rules()
        assert "CODE.no-comments" in rules
        assert "CONFIG.complete" not in rules
        assert rules < validator.rule_ids()

    def test_offered_questions_carry_the_global_mode_and_shadow_capability(
        self,
    ) -> None:
        offered = validator.offered_questions()
        assert not validator.NOT_CONFIGURABLE & set(offered)
        assert offered["structure-naming/consumer_placement"] == OfferedQuestion(
            "shadow", True
        )
        assert offered["extension-impact/impact"].mode == "enforced"

    def test_shadow_capability_is_the_store_s_shared_rule(self) -> None:
        assert decision_store.shadow_capable("bool", {})
        assert decision_store.shadow_capable("choice", {"threshold": 0.5})
        assert not decision_store.shadow_capable("choice", {"round_up_floor": 0.2})

    def test_github_origins_parse_to_a_lowercase_slug(self) -> None:
        for url in (
            "https://github.com/Example-Owner/Ledger.git",
            "git@github.com:example-owner/ledger",
            "ssh://git@github.com/example-owner/ledger.git/\n",
        ):
            with self.subTest(url=url):
                assert validator.parse_origin(url) == "example-owner/ledger"
        assert validator.parse_origin("https://gitlab.com/a/b.git") is None
        assert validator.parse_origin("git@github.com:a/..") is None


class Reading(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)

    def test_a_missing_file_is_missing(self) -> None:
        path = self.temp / "config.json"
        with pytest.raises(ConfigError) as caught:
            validator.read_config(path)
        assert (caught.value.kind, caught.value.path) == ("missing", path)
        assert str(caught.value) == f"config missing: {path} does not exist"

    def test_an_unreadable_file_is_invalid(self) -> None:
        path = self.temp / "config.json"
        path.write_bytes(b"\xff\xfe")
        with pytest.raises(ConfigError) as caught:
            validator.read_config(path)
        assert caught.value.kind == "invalid"
        assert "cannot be read" in caught.value.problems[0]


class Command(unittest.TestCase):
    def test_file_form_validates_a_whole_config_against_an_optional_slug(
        self,
    ) -> None:
        path = FIXTURES / "valid/minimal.json"
        assert run_cli("--file", str(path)) == (
            0,
            {"ok": True, "kind": "ok", "path": str(path), "problems": []},
        )
        code, result = run_cli("--file", str(path), "--slug", "example-owner/other")
        assert (code, result["kind"]) == (1, "invalid")
        assert result["problems"][0].startswith("repo.slug: is 'example-owner/ledger'")

    def test_file_form_reports_missing_and_malformed_files(self) -> None:
        missing = FIXTURES / "absent.json"
        assert run_cli("--file", str(missing)) == (
            1,
            {
                "ok": False,
                "kind": "missing",
                "path": str(missing),
                "problems": [f"{missing} does not exist"],
            },
        )
        code, result = run_cli("--file", str(FIXTURES / "invalid/malformed-json.json"))
        assert (code, result["kind"], result["ok"]) == (1, "invalid", False)

    def test_section_form_validates_one_section_value(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "commands.json"
        path.write_text(json.dumps({"commit": ["make", "gate"]}))
        assert run_cli("--section", "commands", "--file", str(path))[0] == 0
        path.write_text(json.dumps({"commit": "make gate"}))
        code, result = run_cli("--section", "commands", "--file", str(path))
        assert (code, result["problems"]) == (
            1,
            ["commands.commit: must be a non-empty argv array of non-blank strings"],
        )

    def test_list_rules_names_every_switchable_code_rule_once(self) -> None:
        code, result = run_cli("--list-rules")
        ids = [rule["id"] for rule in result["rules"]]
        assert code == 0
        assert ids == sorted(validator.code_check_rules())
        assert all(rule["summary"] for rule in result["rules"])

    def test_list_questions_names_the_offered_questions_with_their_modes(
        self,
    ) -> None:
        code, result = run_cli("--list-questions")
        keys = [question["key"] for question in result["questions"]]
        assert code == 0
        assert keys == sorted(validator.offered_questions())
        assert not validator.NOT_CONFIGURABLE & set(keys)
        assert {
            "key": "structure-naming/consumer_placement",
            "mode": "shadow",
            "shadowable": True,
        } in result["questions"]

    def test_misused_options_are_usage_errors(self) -> None:
        cases: tuple[list[str], ...] = (
            ["--repo", ".", "--slug", "a/b"],
            ["--file", "x.json", "--section", "tests", "--slug", "a/b"],
            ["--file", "x.json", "--slug", "A/B"],
            ["--file", "x.json", "--section", "repo"],
            ["--list-rules", "--slug", "a/b"],
            ["--list-questions", "--section", "tests"],
            ["--list-rules", "--list-questions"],
            [],
        )
        for args in cases:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with pytest.raises(SystemExit) as caught:
                    cli.main(["config-check", *args])
                assert caught.value.code == 2


if __name__ == "__main__":
    unittest.main()
