"""Shared test-evidence checkers of the guardrails reference over synthetic JUnit evidence."""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands import cli
from guardrails.commands.test_evidence import budgets, mutation, propagation
from guardrails.toolkit.test_report import collection, junit

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

REFERENCE = ROOT / "agent/extensions/dynaflows/engine"

FIXTURES = ROOT / "tests/fixtures/reference/test_evidence"

REVISION = "0123456789abcdef0123456789abcdef01234567"

PYTEST_LIKE = junit.Dialect(
    name="pytest-like", source_suffix=".py", xfail_markers=("pytest.xfail",)
)

GENERIC = junit.dialect_for(None)

MACHINE = budgets.Machine("m-approved0001", "Linux x86_64", "python 3.12.4")


def profile(**evidence: object) -> dict[str, Any]:
    return {
        "adapters": [
            {
                "name": "pytest_like",
                "tiers": {
                    "t1": {"selector": "unit", "location": "tests/unit"},
                    "t2": {"selector": "flow", "location": "tests/flow"},
                    "t3": {"selector": "explore", "location": "tests/explore"},
                },
            }
        ],
        "tiers": {
            "t1": {"command": "run t1"},
            "t2": {"command": "run t2"},
            "t3": {"command": "run t3"},
        },
        "layout": {
            "support_dir": "tests/support",
            "fixtures_dir": "tests/data",
            "test_file_patterns": ["test_*.py"],
        },
        "fast_tier_budget": {
            "duration_report_command": "run t1 --durations",
            "machines": [
                {
                    "id": MACHINE.id,
                    "platform": MACHINE.platform,
                    "runtime": MACHINE.runtime,
                    "wall_limit_s": 2.0,
                    "per_test_limit_s": 0.5,
                    "approval": "owner approval 2026-11-01",
                }
            ],
        },
        "guardrails": {"test_evidence": evidence},
    }


def fixture(name: str, dialect: junit.Dialect = PYTEST_LIKE) -> junit.Report:
    return junit.parse_report(FIXTURES / name, dialect, name)


def edited(
    name: str, old: str, new: str, dialect: junit.Dialect = PYTEST_LIKE
) -> junit.Report:
    text = (FIXTURES / name).read_text()
    if old not in text:
        raise AssertionError(f"{old!r} is not in {name}")
    return junit.parse_text(text.replace(old, new), dialect, name)


def rules(findings: list[Any]) -> list[str]:
    return sorted(finding.rule_id for finding in findings)


class JunitNormalisationTests(unittest.TestCase):
    def test_pytest_like_dotted_classnames_become_file_and_class_ids(self) -> None:
        report = fixture("pytest-clean.xml")
        assert [case.id for case in report.cases] == [
            "tests/test_parser.py::TestParse::test_reads_header",
            "tests/test_parser.py::TestParse::test_rejects_empty[blank]",
            "tests/unit/test_cli.py::test_help_exits_zero",
        ]
        assert [case.duration_s for case in report.cases] == [0.12, 0.03, 0.4]
        assert report.wall_s == 1.4
        assert report.properties["omp.runtime"] == "python 3.12.4"
        assert report.declared == {"tests": 3, "failures": 0, "errors": 0}

    def test_pytest_like_outcomes_reasons_and_retries_are_normalised(self) -> None:
        cases = fixture("pytest-mixed.xml").cases
        assert [(case.outcome, case.retries) for case in cases] == [
            ("passed", 0),
            ("failed", 0),
            ("skipped", 0),
            ("xfail", 0),
            ("passed", 1),
            ("error", 0),
        ]
        assert cases[2].reason == "needs locale"
        assert cases[5].id == "::tests.test_broken"

    def test_vitest_like_path_classnames_keep_the_describe_chain(self) -> None:
        report = fixture("vitest-clean.xml", GENERIC)
        assert report.cases[0].id == "src/parser.test.ts::parser > reads a header"
        assert report.cases[2].file == "src/cli.test.ts"
        assert report.wall_s == 0.912
        assert report.declared["tests"] == 3
        assert report.properties["omp.runtime"] == "node 22.3.0"

    def test_vitest_like_skips_and_retry_properties_are_normalised(self) -> None:
        cases = fixture("vitest-mixed.xml", GENERIC).cases
        assert [case.outcome for case in cases] == [
            "passed",
            "failed",
            "skipped",
            "passed",
        ]
        assert cases[3].retries == 2

    def test_file_attribute_wins_over_the_classname(self) -> None:
        text = (
            '<testsuite tests="1"><testcase file="./tests/test_a.py" '
            'classname="tests.test_a.TestA" name="test_x" time="1"/></testsuite>'
        )
        case = junit.parse_text(text, GENERIC, "r.xml").cases[0]
        assert case.id == "tests/test_a.py::TestA::test_x"

    def test_unusable_reports_raise_report_errors(self) -> None:
        with pytest.raises(junit.ReportError, match="well-formed"):
            junit.parse_text("<testsuite>", GENERIC, "r.xml")
        with pytest.raises(junit.ReportError, match="root"):
            junit.parse_text("<html/>", GENERIC, "r.xml")
        with pytest.raises(junit.ReportError, match="does not exist"):
            junit.parse_report(FIXTURES / "absent.xml", GENERIC)

    def test_malformed_times_normalise_to_none(self) -> None:
        text = (
            '<testsuite><testcase classname="a/b.ts" name="x" time="nan"/></testsuite>'
        )
        assert junit.parse_text(text, GENERIC, "r.xml").cases[0].duration_s is None

    def test_profile_data_configures_the_generic_dialect(self) -> None:
        dialect = junit.dialect_for(
            "pytest-like", {"source_suffix": ".py", "xfail_markers": ["pytest.xfail"]}
        )
        assert dialect.source_suffix == ".py"
        assert dialect.xfail_markers == ("pytest.xfail",)
        with pytest.raises(junit.UsageError, match="unknown fields"):
            junit.dialect_for(None, {"suffix": ".py"})

    def test_an_adapter_without_a_dialect_module_uses_the_generic_dialect(self) -> None:
        assert junit.dialect_for("no_such_adapter").name == "generic"

    def test_adapters_register_dialects_that_override_hooks(self) -> None:
        class Upper(junit.Dialect):
            def case_name(self, case: ElementTree.Element, file: str) -> str:
                return case.get("name", "").upper()

        junit.register_dialect(Upper(name="upper-test"))
        self.addCleanup(junit.DIALECTS.pop, "upper-test")
        text = '<testsuite><testcase classname="a/b.ts" name="x"/></testsuite>'
        report = junit.parse_text(text, junit.dialect_for("upper-test"), "r.xml")
        assert report.cases[0].id == "a/b.ts::X"

    def test_report_options_reject_bad_arguments(self) -> None:
        repo = Path("/repo")
        parsed = junit.report_options(
            repo, ["--junit", "r.xml", "--exit-code=5", "--tier", "t1=x"]
        )
        assert (parsed.junit, parsed.exit_code, parsed.tier) == (
            repo / "r.xml",
            5,
            "t1=x",
        )
        for argv in (["--exit-code", "five"], ["--junit"]):
            with pytest.raises(junit.UsageError):
                junit.report_options(repo, argv)


class MachineProbeTests(unittest.TestCase):
    def test_machine_id_is_deterministic_and_changes_with_each_input(self) -> None:
        base = {"system": "Linux", "architecture": "x86_64", "cpu": "Example CPU"}
        first = budgets.machine_id("python 3.12.4", **base)
        assert re.search(r"^m-[0-9a-f]{12}$", first)
        assert first == budgets.machine_id("python  3.12.4", **base)
        assert first != budgets.machine_id("python 3.13.0", **base)
        assert first != budgets.machine_id(
            "python 3.12.4", **{**base, "cpu": "Other CPU"}
        )


class BudgetTests(unittest.TestCase):
    def evaluate(
        self,
        report: junit.Report,
        machine: budgets.Machine = MACHINE,
        **evidence: object,
    ) -> budgets.BudgetResult:
        return budgets.evaluate(report, profile(**evidence), REVISION, machine)

    def test_an_approved_machine_within_limits_passes(self) -> None:
        result = self.evaluate(fixture("pytest-clean.xml"))
        assert (result.findings, result.notes) == ([], [])

    def test_a_slow_test_blocks_locally_with_a_speed_up_remedy(self) -> None:
        report = edited("pytest-clean.xml", 'time="0.400"', 'time="0.900"')
        findings = self.evaluate(report).findings
        assert rules(findings) == ["TEST.budget.per-test-time"]
        assert (
            "tests/unit/test_cli.py::test_help_exits_zero took 0.900s"
            in findings[0].message
        )
        assert "inject" not in findings[0].fix
        assert "never relabel" in findings[0].fix

    def test_a_slow_fast_tier_blocks_locally(self) -> None:
        report = edited("pytest-clean.xml", 'value="1.400"', 'value="2.500"')
        assert rules(self.evaluate(report).findings) == [budgets.FAST_TIER]

    def test_an_unknown_machine_is_report_only_and_prompts_for_a_baseline(self) -> None:
        report = edited("pytest-clean.xml", 'value="1.400"', 'value="9.000"')
        stranger = budgets.Machine("m-unknown00001", "Darwin arm64", "python 3.12.4")
        result = self.evaluate(report, machine=stranger)
        assert result.findings == []
        assert "Approve a fast-tier wall limit" in result.notes[0]
        assert "m-unknown00001" in result.notes[0]

    def test_a_machine_without_approval_is_report_only(self) -> None:
        unapproved = profile()
        unapproved["fast_tier_budget"]["machines"][0]["approval"] = ""
        report = edited("pytest-clean.xml", 'value="1.400"', 'value="9.000"')
        result = budgets.evaluate(report, unapproved, REVISION, MACHINE)
        assert result.findings == []
        assert "no approved budget" in result.notes[0]

    def test_invalid_or_stale_reports_are_fatal(self) -> None:
        cases = {
            "stale": ("pytest-clean.xml", REVISION, "f" * 40, "stale"),
            "no revision": (
                "pytest-clean.xml",
                'name="omp.revision"',
                'name="other"',
                "omp.revision",
            ),
            "no runtime": (
                "pytest-clean.xml",
                'name="omp.runtime"',
                'name="other"',
                "omp.runtime",
            ),
            "untimed": (
                "pytest-clean.xml",
                'time="0.120"',
                'time="soon"',
                "lack a time",
            ),
            "no wall": ("vitest-clean.xml", 'time="0.912"', "", "wall time"),
            "empty": ("vitest-clean.xml", "<testcase", "<ignored", "no test cases"),
        }
        for name, (source, old, new, expected) in cases.items():
            report = edited(source, old, new, GENERIC)
            with self.subTest(name=name):
                findings = self.evaluate(report).findings
                assert findings, name
                assert set(rules(findings)) == {budgets.FAST_TIER}
                assert any(expected in item.message for item in findings), findings

    def test_an_undeterminable_revision_is_fatal(self) -> None:
        result = budgets.evaluate(fixture("pytest-clean.xml"), profile(), None, MACHINE)
        assert "cannot be determined" in result.findings[0].message

    def test_skipped_cases_need_no_time(self) -> None:
        report = edited(
            "pytest-clean.xml",
            '<testcase classname="tests.test_parser.TestParse" name="test_reads_header" time="0.120"/>',
            '<testcase classname="tests.test_parser.TestParse" name="test_reads_header"><skipped/></testcase>',
        )
        assert self.evaluate(report).findings == []


class RepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-evidence-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        self.record = Path(scratch.name).resolve() / "record"
        self.repo.mkdir()
        self.record.mkdir()

    def write(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_profile(self, values: dict[str, Any]) -> None:
        text = f"---\n{json.dumps(values)}\n---\n# Repo test profile\n"
        (self.record / "profile.md").write_text(text)

    def copy_fixture(self, name: str, old: str = "", new: str = "") -> str:
        self.write(
            f"reports/{name}",
            (FIXTURES / name).read_text().replace(old, new)
            if old
            else (FIXTURES / name).read_text(),
        )
        return f"reports/{name}"

    def cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            location = ["--repo", str(self.repo), "--record", str(self.record)]
            code = cli.main([argv[0], *location, *argv[1:]])
        return code, out.getvalue(), err.getvalue()


class BudgetCommandTests(RepoTestCase):
    def test_report_check_never_reads_test_timing(self) -> None:
        self.write_profile(
            profile(dialect={"source_suffix": ".py", "xfail_markers": ["pytest.xfail"]})
        )
        report = self.copy_fixture("pytest-clean.xml", 'value="1.400"', 'value="9.000"')
        code, out, err = self.cli(
            "report-check", "--junit", report, "--revision", "stale"
        )
        assert (code, out.strip(), err) == (0, "report-check: ok", "")

    def test_each_check_blocks_a_missing_report_with_its_own_rule(self) -> None:
        self.write_profile(profile())
        for subcommand, rule in (
            ("report-check", "TEST.failure-propagation"),
            ("budget-check", "TEST.budget.fast-tier-time"),
        ):
            with self.subTest(subcommand=subcommand):
                code, out, _ = self.cli(subcommand, "--junit", "reports/absent.xml")
                assert code == 1
                assert rule in out
                assert (
                    "TEST.failure-propagation"
                    if rule != "TEST.failure-propagation"
                    else "TEST.budget"
                ) not in out

    def test_budgets_skip_reports_of_slower_tiers(self) -> None:
        report = self.copy_fixture("pytest-clean.xml", 'value="t1"', 'value="t2"')
        argv = ["--junit", report, "--revision", "stale"]
        assert budgets.run(self.repo, profile(), argv) == []


class TierCollectionTests(unittest.TestCase):
    def test_an_exempt_t3_tier_needs_no_collect_only_list(self) -> None:
        lists: dict[str, list[str] | None] = {
            "t1": ["tests/unit/test_a.py::test_one"],
            "t2": ["tests/flow/test_b.py::test_two"],
        }
        assert [
            item.message
            for item in collection.collection_findings(lists, profile(), [])
        ] == ["tier t3 has no collect-only list"]
        exempt = profile()
        exempt["tiers"]["t3"] = {"none": "no exploratory suites", "approval": "ADR-9"}
        assert collection.collection_findings(lists, exempt, []) == []


class PropagationTests(unittest.TestCase):
    def test_a_clean_run_passes(self) -> None:
        assert propagation.evaluate(fixture("pytest-clean.xml"), profile(), None) == []
        assert (
            propagation.evaluate(fixture("vitest-clean.xml", GENERIC), profile(), 0)
            == []
        )

    def test_failures_errors_skips_xfails_and_retries_are_refused(self) -> None:
        findings = propagation.evaluate(fixture("pytest-mixed.xml"), profile(), None)
        messages = {(item.rule_id, item.message.split(" ", 1)[1]) for item in findings}
        assert (
            "TEST.failure-propagation",
            "failed: AssertionError: expected ValueError",
        ) in messages
        assert (
            "TEST.failure-propagation",
            "errored (infrastructure or collection): collection failure",
        ) in messages
        assert (
            "TEST.no-evasion",
            "is skipped without approval (needs locale)",
        ) in messages
        assert (
            "TEST.no-evasion",
            "is xfail without approval (issue pending)",
        ) in messages
        assert ("TEST.no-evasion", "passed only after 1 retries") in messages
        assert len(findings) == 5

    def test_vitest_like_failures_skips_and_retries_are_refused(self) -> None:
        findings = propagation.evaluate(
            fixture("vitest-mixed.xml", GENERIC), profile(), None
        )
        assert rules(findings) == [
            "TEST.failure-propagation",
            "TEST.no-evasion",
            "TEST.no-evasion",
        ]

    def test_approved_skips_pass_and_incomplete_approvals_do_not(self) -> None:
        report = fixture("pytest-mixed.xml")
        entry = {
            "id": "tests/test_parser.py::test_unicode",
            "reason": "locale absent in CI",
            "approval": "ADR-12",
        }
        accepted = propagation.evaluate(report, profile(approved_skips=[entry]), None)
        assert not any("test_unicode" in item.message for item in accepted)
        refused = propagation.evaluate(
            report, profile(approved_skips=[{**entry, "approval": " "}]), None
        )
        assert any("test_unicode" in item.message for item in refused)

    def test_truncated_reports_are_refused(self) -> None:
        report = edited("pytest-clean.xml", 'tests="3"', 'tests="4"')
        findings = propagation.evaluate(report, profile(), None)
        assert [item.message for item in findings] == [
            "suites declare 4 tests but the report holds 3"
        ]

    def test_runner_exit_codes_must_agree_with_the_cases(self) -> None:
        clean = fixture("pytest-clean.xml")
        assert "exited 5" in propagation.evaluate(clean, profile(), 5)[0].message
        assert (
            "exited 2"
            in propagation.evaluate(
                edited("pytest-clean.xml", 'value="0"', 'value="2"'), profile(), None
            )[0].message
        )
        failing = edited(
            "vitest-clean.xml",
            'time="0.004"/>',
            'time="0.004"><failure message="x"/></testcase>',
            GENERIC,
        )
        assert any(
            "exited 0" in item.message
            for item in propagation.evaluate(failing, profile(), 0)
        )

    def test_empty_reports_never_pass(self) -> None:
        empty = junit.parse_text('<testsuites tests="0"/>', GENERIC, "r.xml")
        assert [item.message for item in propagation.evaluate(empty, profile(), 0)] == [
            "run report holds no test cases"
        ]

    def test_duplicate_ids_are_ambiguous_unless_they_are_a_retry_to_green(self) -> None:
        twice = '<testsuite><testcase classname="a/b.ts" name="x"/><testcase classname="a/b.ts" name="x"/></testsuite>'
        assert (
            "reported 2 times"
            in propagation.evaluate(
                junit.parse_text(twice, GENERIC, "r"), profile(), None
            )[0].message
        )
        retried = twice.replace('name="x"/>', 'name="x"><failure/></testcase>', 1)
        rules_found = rules(
            propagation.evaluate(
                junit.parse_text(retried, GENERIC, "r"), profile(), None
            )
        )
        assert rules_found == ["TEST.no-evasion"]

    def test_missing_reports_and_bad_arguments_are_findings(self) -> None:
        missing = propagation.run(Path("/nonexistent"), profile(), ["--junit", "r.xml"])
        assert missing[0].message == "run report does not exist"
        assert (
            "--junit" in propagation.run(Path("/nonexistent"), profile(), [])[0].message
        )
        assert (
            "usage"
            in propagation.run(Path("/nonexistent"), profile(), ["--exit-code", "x"])[
                0
            ].message
        )


class MutationSensitivityTests(unittest.TestCase):
    """TEST.mutation-sensitivity over synthetic normalised mutation reports."""

    ADDED = {"src/app.py": {4, 5}}

    @staticmethod
    def mutant(status: str, start: int = 3, end: int = 6) -> mutation.Mutant:
        return mutation.Mutant(
            f"app.x_run__mutmut_{status}", "src/app.py", start, end, status
        )

    def test_surviving_and_untested_mutants_in_changed_code_fail(self) -> None:
        found = mutation.check(
            [
                self.mutant("survived"),
                self.mutant("no_tests"),
                self.mutant("incomplete"),
            ],
            self.ADDED,
        )
        assert [f.rule_id for f in found] == ["TEST.mutation-sensitivity"] * 3
        assert "survived in changed code" in found[0].message

    def test_killed_or_unchanged_mutants_pass(self) -> None:
        mutants = [
            self.mutant("killed"),
            self.mutant("timeout"),
            self.mutant("survived", start=10, end=12),
            mutation.Mutant("other", "src/other.py", 1, 9, "survived"),
        ]
        assert mutation.check(mutants, self.ADDED) == []

    def test_missing_or_malformed_report_never_passes(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            assert (
                "pass --report" in mutation.run(repo, {}, ["--base", "HEAD"])[0].message
            )
            bad = repo / "report.json"
            bad.write_text(json.dumps({"version": 1, "mutants": [{"id": "x"}]}))
            found = mutation.run(repo, {}, ["--report", str(bad), "--base", "HEAD"])
            assert [f.rule_id for f in found] == ["TEST.mutation-sensitivity"]
            assert "does not follow the report schema" in found[0].message


if __name__ == "__main__":
    unittest.main()
