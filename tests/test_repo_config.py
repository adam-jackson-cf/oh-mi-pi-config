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
from guardrails.commands.check import whole_repo
from guardrails.context.config import profile, repo_config
from guardrails.context.config.repo_config import ConfigError, OfferedQuestion
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
        config = repo_config.read_config(path)
    except ConfigError as error:
        return error.kind, error.problems
    return "invalid", repo_config.validate(config, origin=None)


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
                assert repo_config.validate(config, origin=origin) == []

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
        assert repo_config.validate(config, origin="example-owner/other") == [
            "repo.slug: is 'example-owner/ledger' but the origin remote names "
            "'example-owner/other'"
        ]
        assert repo_config.validate(config, origin=None) == []

    def test_a_config_that_is_not_an_object_is_refused(self) -> None:
        assert repo_config.validate([], origin=None) == [
            "config: must be a JSON object"
        ]

    def test_shadow_is_refused_for_a_question_without_a_non_blocking_outcome(
        self,
    ) -> None:
        setting = {"enabled": True, "mode": "shadow"}
        assert repo_config._mode_problem(
            OfferedQuestion("enforced", shadowable=False), setting
        ) == (
            "cannot run in shadow mode: only a bool answer or a choice option "
            "threshold has a non-blocking outcome"
        )
        assert (
            repo_config._mode_problem(OfferedQuestion("enforced", True), setting)
            is None
        )
        assert (
            repo_config._mode_problem(
                OfferedQuestion("shadow", True), {"enabled": False}
            )
            is None
        )

    def test_a_malformed_judgment_setting_reports_its_shape(self) -> None:
        config = fixture("minimal.json")
        config["judgments"] = {
            "extension-impact/impact": {"enabled": True},
            "completion-evidence/evidence_gap": {"enabled": "no"},
        }
        assert repo_config.validate(config, origin=None) == [
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
        assert repo_config.validate(config, origin=None) == [
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
        config["onboarding"]["skills"] = {
            "test-guidance": {
                "version": "ABC",
                "ranAt": "2026-13-40T99:00:00Z",
                "scope": {"kind": "paths", "paths": [" "]},
            }
        }
        config["afterMerge"] = {"argv": ["make"], "paths": []}
        config["mergePolicies"][0]["headPrefix"] = 3
        config["checkers"]["check"]["run"] = []
        config["checkers"]["naming"] = []
        config["exceptions"][0]["approvedBy"] = " "
        config["exceptions"].append("NAME.conventions")
        assert repo_config.validate(config, origin=None) == [
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
            f"{list(repo_config.CHECK_STEPS)}",
            "exceptions[0].approvedBy: must be a non-blank string",
            "exceptions[1]: must be an object",
        ]

    def test_an_unknown_onboarding_origin_and_skill_scope_name_their_options(
        self,
    ) -> None:
        config = fixture("minimal.json")
        config["onboarding"]["skills"]["setup-repo-proofs"]["scope"] = {"kind": "all"}
        config["autonomy"] = []
        assert repo_config.validate(config, origin=None) == [
            "onboarding.skills.setup-repo-proofs.scope.kind: must be one of "
            '["whole", "changed", "paths"]',
            "autonomy: must be an object",
        ]
        config["onboarding"] = {"origin": "import"}
        assert repo_config.validate(config, origin=None)[0] == (
            'onboarding.origin: must be one of ["onboarding", "migration"]'
        )

    def test_migration_onboarding_names_its_source_and_may_omit_skill_runs(
        self,
    ) -> None:
        config = fixture("full.json")
        assert config["onboarding"]["origin"] == "migration"
        del config["onboarding"]["migratedFrom"]
        assert repo_config.validate(config, origin=None) == [
            "onboarding.migratedFrom: is missing"
        ]

    def test_an_exemption_approval_must_name_an_exemptable_field(self) -> None:
        config = fixture("minimal.json")
        config["approvals"].append({
            **config["approvals"][0],
            "kind": "exemption",
            "target": "coverage",
        })
        assert repo_config.validate(config, origin=None) == [
            "approvals[1].target: must name an exemptable field "
            f"{list(repo_config.EXEMPTION_TARGETS)}"
        ]

    def test_tests_reuse_the_profile_adapter_rules(self) -> None:
        config = fixture("minimal.json")
        config["tests"]["adapters"][0]["name"] = "nose"
        assert repo_config.validate(config, origin=None) == [
            "tests.adapters[0].name: 'nose' is not an installed adapter package"
        ]


class Sections(unittest.TestCase):
    def test_each_checked_section_of_the_full_fixture_validates_alone(self) -> None:
        config = fixture("full.json")
        for name in repo_config.CHECKED_SECTIONS:
            with self.subTest(section=name):
                assert repo_config.validate_section(name, config[name]) == []

    def test_a_section_reports_problems_under_its_own_name(self) -> None:
        tests = copy.deepcopy(fixture("minimal.json")["tests"])
        tests["fast_tier_budget"]["machines"][0]["approval"] = "ADR-0008"
        assert repo_config.validate_section("tests", tests) == [
            "tests.fast_tier_budget.machines[0].approval: is refused: owner approvals "
            "live only in the approvals section"
        ]
        assert repo_config.validate_section("tests", []) == ["tests: must be an object"]

    def test_sections_outside_the_checked_set_are_refused(self) -> None:
        with pytest.raises(ValueError, match="'approvals' is not a section"):
            repo_config.validate_section("approvals", [])


class Constants(unittest.TestCase):
    def test_sections_and_targets_match_the_engine_tables(self) -> None:
        assert len(repo_config.SECTIONS) == 16
        assert set(repo_config.CHECKED_SECTIONS) < set(repo_config.SECTIONS)
        assert set(repo_config.EXEMPTION_TARGETS) == profile.EXEMPTABLE
        assert whole_repo.DEFAULT_STEPS == (
            "profile-check",
            "python-quality",
            "python-fanout",
            "python-scalability",
            "surface-lint",
            "adapter lint",
        )
        assert repo_config.GATE_KEYS[-1] == "rules"

    def test_code_check_rules_leave_out_process_rules(self) -> None:
        rules = repo_config.code_check_rules()
        assert "CODE.no-comments" in rules
        assert "PROFILE.complete" not in rules
        assert rules < repo_config.rule_ids()

    def test_offered_questions_carry_the_global_mode_and_shadow_capability(
        self,
    ) -> None:
        offered = repo_config.offered_questions()
        assert not repo_config.NOT_CONFIGURABLE & set(offered)
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
                assert repo_config.parse_origin(url) == "example-owner/ledger"
        assert repo_config.parse_origin("https://gitlab.com/a/b.git") is None
        assert repo_config.parse_origin("git@github.com:a/..") is None


class Reading(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)

    def test_a_missing_file_is_missing(self) -> None:
        path = self.temp / "config.json"
        with pytest.raises(ConfigError) as caught:
            repo_config.read_config(path)
        assert (caught.value.kind, caught.value.path) == ("missing", path)
        assert str(caught.value) == f"config missing: {path} does not exist"

    def test_an_unreadable_file_is_invalid(self) -> None:
        path = self.temp / "config.json"
        path.write_bytes(b"\xff\xfe")
        with pytest.raises(ConfigError) as caught:
            repo_config.read_config(path)
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

    def test_misused_options_are_usage_errors(self) -> None:
        cases: tuple[list[str], ...] = (
            ["--repo", ".", "--slug", "a/b"],
            ["--file", "x.json", "--section", "tests", "--slug", "a/b"],
            ["--file", "x.json", "--slug", "A/B"],
            ["--file", "x.json", "--section", "approvals"],
            [],
        )
        for args in cases:
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with pytest.raises(SystemExit) as caught:
                    cli.main(["config-check", *args])
                assert caught.value.code == 2


if __name__ == "__main__":
    unittest.main()
