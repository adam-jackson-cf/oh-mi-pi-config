"""The packaged rule catalogue, its coverage by these tests, toolchain parity and versions.

Every rule the package proves is named by a test here.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import unittest
from importlib import resources
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands.catalogue import versions
from guardrails.context.config import adapter_declarations
from guardrails.foundation import child_env
from guardrails.toolkit import dispatch

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

REFERENCE = ROOT / "agent/extensions/dynaflows/engine"

# Tool rules whose values tests/test_reference_init.py NativeConfigTests and
# LanguageNativeConfigTests check against the data init writes.
NATIVE_PREFIXES = (
    "RUFF.",
    "MYPY.",
    "TSC.",
    "OXLINT.",
    "GOLANGCI.",
    "SHELLCHECK.",
    "SHFMT.",
)


def names_rule(sources: list[Path], rule_id: str) -> bool:
    return any(rule_id in source.read_text(encoding="utf-8") for source in sources)


def imported_sources(sources: list[Path]) -> list[Path]:
    return [
        source
        for imported in reference_imports(sources)
        for source in module_sources(imported)
    ]


def packaged_catalogue() -> dict[str, Any]:
    data = resources.files("guardrails.data").joinpath("guardrail-rules.json")
    catalogue: dict[str, Any] = json.loads(data.read_bytes())
    return catalogue


def proven_by_package(rule: dict[str, Any]) -> bool:
    return rule.get("kind") == "tool" or bool(rule.get("reference"))


PLACEHOLDER = re.compile(
    r"\b(todo|tbd|tbc|fixme|xxx|placeholder|lorem|n/a)\b|\.\.\.|<[^>]*>", re.IGNORECASE
)
REMEDIATION_FIELDS = ("root_cause", "fix")
MINIMUM_WORDS = 8


def remediation_problems(entry: dict[str, Any]) -> list[str]:
    remediation = entry.get("remediation")
    if not isinstance(remediation, dict) or set(remediation) != set(REMEDIATION_FIELDS):
        return ["remediation must hold exactly root_cause and fix"]
    problems = []
    for field in REMEDIATION_FIELDS:
        text = remediation[field]
        if not isinstance(text, str) or len(text.split()) < MINIMUM_WORDS:
            problems.append(f"{field} must state at least {MINIMUM_WORDS} words")
        elif PLACEHOLDER.search(text):
            problems.append(f"{field} holds placeholder text")
        elif text in {entry.get("summary"), entry.get("message")}:
            problems.append(f"{field} repeats the rule's summary or message")
    if remediation["root_cause"] == remediation["fix"]:
        problems.append("root_cause and fix must differ")
    return problems


def repeated_remediations(entries: list[dict[str, Any]]) -> list[str]:
    repeated: list[str] = []
    for field in REMEDIATION_FIELDS:
        texts = [entry["remediation"][field] for entry in entries]
        repeated.extend(text for text in set(texts) if texts.count(text) > 1)
    return repeated


def adapter_rule_files() -> dict[str, list[dict[str, Any]]]:
    adapters = REFERENCE / "guardrails/commands/adapters"
    files = {
        path.relative_to(adapters).as_posix(): [json.loads(path.read_text())]
        for path in sorted((adapters / "pytest/rules").glob("*.yml"))
    }
    files["vitest/patterns.yml"] = json.loads(
        (adapters / "vitest/patterns.yml").read_text()
    )
    return files


def entry_keys(entries: list[dict[str, Any]]) -> set[str]:
    return {key for entry in entries for key in entry}


class RemediationSchemaTests(unittest.TestCase):
    def test_every_catalogue_rule_states_root_cause_and_fix(self) -> None:
        rules = packaged_catalogue()["rules"]
        for rule in rules:
            with self.subTest(rule=rule["id"]):
                assert remediation_problems(rule) == []
        assert repeated_remediations(rules) == []

    def test_every_adapter_pattern_states_root_cause_and_fix(self) -> None:
        for name, entries in adapter_rule_files().items():
            with self.subTest(file=name):
                assert "fix" not in entry_keys(entries)
                for entry in entries:
                    assert remediation_problems(entry) == [], entry["id"]
                assert repeated_remediations(entries) == []

    def test_missing_short_or_placeholder_remediations_fail_the_schema(self) -> None:
        cause = "The rule fired because the code took a shortcut around its owner."
        fix = (
            "Route the behaviour through its owner and delete the shortcut everywhere."
        )
        assert (
            remediation_problems({"remediation": {"root_cause": cause, "fix": fix}})
            == []
        )
        assert remediation_problems({}) == [
            "remediation must hold exactly root_cause and fix"
        ]
        assert remediation_problems({"remediation": {"root_cause": cause}}) == [
            "remediation must hold exactly root_cause and fix"
        ]
        assert remediation_problems({
            "remediation": {"root_cause": "TODO", "fix": fix}
        }) == ["root_cause must state at least 8 words"]
        assert remediation_problems({
            "remediation": {
                "root_cause": cause,
                "fix": "TBD: write the real fix for this rule later on.",
            }
        }) == ["fix holds placeholder text"]
        assert remediation_problems({
            "summary": fix,
            "remediation": {"root_cause": cause, "fix": fix},
        }) == ["fix repeats the rule's summary or message"]
        assert remediation_problems({
            "remediation": {"root_cause": cause, "fix": cause}
        }) == ["root_cause and fix must differ"]
        assert repeated_remediations([
            {"remediation": {"root_cause": cause, "fix": fix}},
            {"remediation": {"root_cause": cause, "fix": fix + " Again."}},
        ]) == [cause]


class PackagedCatalogueTests(unittest.TestCase):
    """The catalogue ships in the package; its reference rules resolve to emitting modules."""

    def setUp(self) -> None:
        self.catalogue = packaged_catalogue()

    def test_catalogue_ships_as_package_data(self) -> None:
        assert self.catalogue["schema_version"] == 1
        ids = [rule["id"] for rule in self.catalogue["rules"]]
        assert len(ids) == len(set(ids))
        for rule in self.catalogue["rules"]:
            with self.subTest(rule=rule["id"]):
                assert rule["kind"] in {"bespoke", "process", "tool"}
                assert rule["applicability"] in {"always", "conditional"}
                assert isinstance(rule["summary"], str)
                assert rule["summary"]

    def test_path_check_contract_is_a_harness_subcommand_over_config_settings(
        self,
    ) -> None:
        rule = next(
            rule for rule in self.catalogue["rules"] if rule["id"] == "GATE.path-check"
        )
        contract = rule["contract"]
        assert contract["configuration"] == ".agentrails/config.json#checkers"
        assert contract["harness_subcommand"] == "guardrails path-check"
        assert "required_command_field" not in contract
        assert contract["changed_paths_appended_as_arguments"]
        assert contract["success_exit_code"] == 0
        assert contract["violation_exit_nonzero"]

    def test_naming_contract_limits_events_to_new_path_segments(self) -> None:
        rules = {rule["id"]: rule["contract"] for rule in self.catalogue["rules"]}
        assert (
            "content edits do not trigger naming"
            in rules["NAME.conventions"]["event_scope"]
        )
        assert "legacy parent" in rules["NAME.conventions"]["segment_scope"]
        assert "full scans" in rules["NAME.test-files"]["event_scope"]
        exceptions = rules["PROFILE.complete"]["guardrail_exceptions"]
        assert exceptions["field"] == "exceptions"
        assert exceptions["required_fields"] == [
            "rule",
            "path",
            "reason",
            "approvedBy",
            "approvedAt",
        ]
        assert exceptions["optional_fields"] == ["check"]
        assert exceptions["unknown_fields_rejected"]

    def test_references_name_subcommands_whose_modules_emit_the_rule(self) -> None:
        for rule in self.catalogue["rules"]:
            for item in rule.get("reference", []):
                subcommand, module = item["subcommand"], item["module"]
                with self.subTest(rule=rule["id"], module=module):
                    assert set(item) == {"subcommand", "module"}
                    assert subcommand in dispatch.SUBCOMMANDS
                    if subcommand == "adapter":
                        assert module.startswith(adapter_declarations.PACKAGE + ".")
                    else:
                        assert module in dispatch.SUBCOMMANDS[subcommand].modules
                    sources = module_sources(module)
                    assert sources, f"missing module {module}"
                    sources += imported_sources(sources)
                    assert names_rule(sources, rule["id"]), (
                        f"{module} never names {rule['id']}"
                    )

    def test_every_package_proven_rule_is_exercised_by_the_skill_tests(self) -> None:
        """Rules the package proves are proven here, once for every repo: each is named by a test."""
        tests = ROOT / "tests"
        sources = [
            *tests.glob("test_reference_*.py"),
            *(tests / "fixtures/reference").rglob("*"),
        ]
        texts = "".join(
            path.read_text(encoding="utf-8")
            for path in sorted(sources)
            if path.is_file() and path.suffix in {".py", ".json", ".md", ".xml"}
        )
        conformance = REFERENCE / "guardrails/commands/adapters/pytest/conformance"
        texts += "".join(
            path.read_text(encoding="utf-8") for path in conformance.glob("*.json")
        )
        for rule in self.catalogue["rules"]:
            if not proven_by_package(rule):
                continue
            with self.subTest(rule=rule["id"]):
                # Native tool rules: init writes the native config the tool enforces, and
                # the native config tests check it against every such catalogue row.
                native = rule["kind"] == "tool" and rule["id"].startswith(
                    NATIVE_PREFIXES
                )
                assert native or rule["id"] in texts, (
                    f"no skill test names {rule['id']}"
                )


class DependencyAuditContractTests(unittest.TestCase):
    """SECURITY.dependency-audit belongs to the repo's checker; the catalogue states its contract."""

    def setUp(self) -> None:
        rows = packaged_catalogue()["rules"]
        self.row = next(
            rule for rule in rows if rule["id"] == "SECURITY.dependency-audit"
        )
        self.contract = self.row["contract"]

    def test_the_repo_checker_owns_the_rule_and_the_harness_runs_no_copy(self) -> None:
        assert "reference" not in self.row
        assert not proven_by_package(self.row)
        assert self.row["applicability"] == "always"
        assert self.contract["setup_outcomes"] == ["repo", "grow"]
        assert self.contract["harness_copy_forbidden"]
        assert "dependency-audit" not in "".join(dispatch.SUBCOMMANDS)

    def test_scope_is_whole_repo_and_fails_closed(self) -> None:
        assert (
            self.contract["scope_source"]
            == "git ls-files --cached --others --exclude-standard"
        )
        for key in (
            "unknown_lock_format_is_failure",
            "manifest_with_dependencies_and_no_lock_is_failure",
            "staged_file_trigger_forbidden",
            "every_advisory_blocks",
            "severity_scoring_in_checker_forbidden",
            "exclusion_by_glob_ecosystem_or_package_forbidden",
        ):
            with self.subTest(key=key):
                assert self.contract[key] is True
        assert self.contract["scheduled_run_by_default"] is False

    def test_the_advisory_line_names_its_fields_in_the_stated_order(self) -> None:
        tokens = self.contract["advisory_line"].split(" ")
        assert tokens[0] == "SECURITY.dependency-audit"
        fields = [token.split("=", 1)[0] for token in tokens[1:]]
        assert fields == self.contract["advisory_fields_in_order"]
        failure = self.contract["failure_line"].split(" ")
        assert [token.split("=", 1)[0] for token in failure[1:4]] == [
            "failure",
            "path",
            "reason",
        ]

    def test_exceptions_are_owner_only_dated_and_only_for_unavailable_fixes(
        self,
    ) -> None:
        exceptions = self.contract["exceptions"]
        assert exceptions["allowed_only_for"] == [
            "advisory_with_no_fix_version",
            "fixed_release_blocked_by_pinned_dependent",
        ]
        assert exceptions["entry_fields"] == [
            "advisory",
            "package",
            "version",
            "lock",
            "reason",
            "exposure",
            "expires",
        ]
        assert exceptions["blocked_fix_fields"] == [
            "blocked_by",
            "constraint",
            "constraint_source",
        ]
        for rule in (
            "suppresses_only_the_exact_advisory_package_version_and_lock",
            "expired_entry_reports_the_advisory_again_and_a_failure",
            "entry_matching_no_reported_advisory_is_failure",
            "entry_for_an_advisory_with_an_unblocked_fix_is_failure",
            "malformed_entry_is_failure",
        ):
            with self.subTest(rule=rule):
                assert exceptions[rule] is True
        for requirement in (
            "same lock",
            "constraint must parse",
            "target the advisory package",
            "exclude every reported fixed version",
            "absolute HTTPS URL",
            "missing blocking fields",
            "missing or changed blocker",
            "any compatible fixed version",
        ):
            with self.subTest(requirement=requirement):
                assert requirement in exceptions["blocked_fix_validation"]
        assert "never fetch" in exceptions["upstream_evidence_attestation"]
        assert "unknown or missing fields fail" in exceptions["entry_validation"]
        assert exceptions["expires_format"] == "YYYY-MM-DD"
        assert "today's UTC date" in exceptions["expires_validation"]
        assert exceptions["agents_never_add_or_edit"] is True
        assert exceptions["file"] == "repo_json_file_the_repo_dependency_audit_reads"


def module_sources(module: str) -> list[Path]:
    """Source files of a reference module: one ``.py`` file, or a package's code and rules."""
    path = REFERENCE.joinpath(*module.split("."))
    if path.is_dir():
        return sorted(
            p for p in path.rglob("*") if p.suffix in {".py", ".yml", ".json"}
        )
    return [path.with_suffix(".py")] if path.with_suffix(".py").is_file() else []


def reference_imports(sources: list[Path]) -> set[str]:
    """``guardrails`` modules imported by the given Python sources."""
    imported: set[str] = set()
    for source in sources:
        if source.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "guardrails."
            ):
                imported.add(node.module or "")
    return imported


class ChildEnvironmentTests(unittest.TestCase):
    def test_repository_commands_never_see_harness_variables(self) -> None:
        with mock.patch.dict(
            os.environ, {"AGENT": "agent-name", "GIT_DIR": "x", "KEEP": "1"}
        ):
            repo_env = child_env.repo_environment()
            clean = child_env.clean_environment(["KEEP"])
        assert "AGENT" not in repo_env
        assert (repo_env["GIT_DIR"], repo_env["KEEP"]) == ("x", "1")
        assert not {"AGENT", "GIT_DIR", "KEEP"} & set(clean)
        assert "AGENT" not in child_env.repo_environment({"AGENT": "a", "PATH": "/bin"})


class VersionMatchingTests(unittest.TestCase):
    def test_pep_440_ordering(self) -> None:
        key = versions.version_key
        ordered = [
            "1.0.dev1",
            "1.0a1",
            "1.0b2",
            "1.0rc1",
            "1.0",
            "1.0.post1.dev1",
            "1.0.post1",
        ]
        assert sorted(ordered, key=key) == ordered
        assert key("1.0") == key("1.0.0")
        assert key("1!0.1") > key("2.0")

    def test_requirement_parsing_and_specifiers(self) -> None:
        requirement = versions.parse_requirement(
            'Foo_Bar[x] >=1.2, <2 ; python_version > "3"'
        )
        assert requirement.name == "foo-bar"
        assert requirement.admits("1.9.9")
        assert not requirement.admits("2.0")
        cases = {
            ("pkg==1.4.*", "1.4.7"): True,
            ("pkg==1.4.*", "1.5"): False,
            ("pkg!=1.4.*", "1.5"): True,
            ("pkg~=1.4.2", "1.4.9"): True,
            ("pkg~=1.4.2", "1.5.0"): False,
            ("pkg~=1.4", "1.9"): True,
            ("pkg===1.0", "1.0"): True,
            ("pkg>1.0", "1.0.post1"): True,
            ("pkg", "0.0.1"): True,
            ("pkg @ https://example.invalid/pkg.whl", "9"): True,
        }
        for (text, version), admitted in cases.items():
            with self.subTest(text=text, version=version):
                assert versions.parse_requirement(text).admits(version) == admitted

    def test_unsupported_input_raises(self) -> None:
        for text in ("", "pkg >> 1", "pkg~=1"):
            with self.subTest(text=text), pytest.raises(versions.VersionError):
                versions.parse_requirement(text).admits("1.0")


if __name__ == "__main__":
    unittest.main()
