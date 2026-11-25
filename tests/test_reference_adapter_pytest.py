from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import config, write_config

from guardrails.commands import cli
from guardrails.commands.adapters import pytest as adapter
from guardrails.commands.adapters.pytest import guards, symbol_usage
from guardrails.commands.adapters.pytest.cases import collect as collect_cases
from guardrails.commands.adapters.pytest.dialect import DIALECT
from guardrails.commands.test_evidence import propagation
from guardrails.foundation.findings import Finding
from guardrails.host.process import run_text
from guardrails.toolkit.test_report.junit import dialect_for, parse_report, parse_text

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]

REFERENCE = ROOT / "agent/extensions/agent-rails/engine"

LOCK = REFERENCE / "requirements-pytest.lock"

CONTAINERFILE = ROOT / ".devcontainer/Containerfile"

FIXTURES = ROOT / "tests/fixtures/reference/adapter_pytest"

RULE_IDS = {
    "TEST.no-self-mocking",
    "TEST.no-test-hooks",
    "TEST.no-evasion",
    "TEST.property-seed",
}


def requirement_entries(text: str) -> dict[str, frozenset[str]]:
    """``{requirement spec: hashes}`` from pip requirement text with ``\\`` continuations."""
    entries = {}
    for line in re.sub(r"\\\n", " ", text).splitlines():
        line = line.split("#", 1)[0] if line.lstrip().startswith("#") else line
        line = re.sub(r"\s#.*$", "", line).strip().strip('"')
        if not line:
            continue
        parts = line.split()
        hashes = frozenset(part for part in parts if part.startswith("--hash="))
        spec = " ".join(part for part in parts if not part.startswith("--hash="))
        entries[spec] = hashes
    return entries


def containerfile_lock() -> dict[str, frozenset[str]]:
    text = re.sub(r"\\\n", " ", CONTAINERFILE.read_text())
    block = next(
        line
        for line in text.splitlines()
        if line.startswith("RUN ") and "requirements-pytest.lock" in line
    )
    quoted = re.findall(r'"([^"]+)"', block.split("> /tmp/requirements-pytest.lock")[0])
    return requirement_entries("\n".join(quoted))


# The fixture config's tests section already declares the pytest adapter.
PYTEST_ONLY: dict[str, Any] = {}


def write_profile(repo: Path, sections: dict[str, Any]) -> Path:
    """Write the repo's .agentrails/config.json: the fixture config with ``sections`` applied."""
    if not (repo / ".git").exists():
        run_text(["git", "init", "-q", str(repo)], check=True, timeout=60)
    return write_config(repo, config(**sections))


def run_cli(*argv: str) -> tuple[int, str]:
    args = list(argv)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(args)
    return code, out.getvalue()


class RequiredExtraTests(unittest.TestCase):
    def test_a_missing_pytest_extra_never_turns_the_adapter_seams_into_skips(
        self,
    ) -> None:
        path = Path(__file__).with_name("test_reference_adapter_pytest_seams.py")
        spec = importlib.util.spec_from_file_location("adapter_seams_probe", path)
        assert spec is not None
        assert spec.loader is not None
        seams = importlib.util.module_from_spec(spec)
        environment = {
            key: value
            for key, value in os.environ.items()
            if key != "OMP_GUARDRAILS_REQUIRE_PYTEST_EXTRA"
        }
        with (
            mock.patch.object(importlib.util, "find_spec", return_value=None),
            mock.patch.dict(os.environ, environment, clear=True),
        ):
            spec.loader.exec_module(seams)
        skipped = [
            f"{owner.__name__}.{name}"
            for owner in vars(seams).values()
            if isinstance(owner, type) and issubclass(owner, unittest.TestCase)
            for name in dir(owner)
            if name.startswith("test")
            and getattr(getattr(owner, name), "__unittest_skip__", False)
        ]
        assert skipped == []


class PinnedExtraTests(unittest.TestCase):
    def test_lock_pins_the_pyproject_extra_with_hashes(self) -> None:
        pyproject = tomllib.loads((REFERENCE / "pyproject.toml").read_text())
        project = pyproject["project"]
        pins = {*project["optional-dependencies"]["pytest"], *project["dependencies"]}
        entries = requirement_entries(LOCK.read_text())
        specs = {spec.split(";")[0].strip() for spec in entries}
        assert pins <= specs
        assert all(entries.values()), "every locked requirement carries a hash"
        assert all("==" in spec for spec in entries), "every requirement is pinned"

    def test_containerfile_installs_exactly_the_lock(self) -> None:
        assert containerfile_lock() == requirement_entries(LOCK.read_text())
        text = CONTAINERFILE.read_text()
        assert "--require-hashes" in text
        assert re.search(r'ENV PATH="/opt/guardrails/bin:\$\{PATH\}"', text)


class StandardLibraryTests(unittest.TestCase):
    def test_shared_parser_loads_the_pytest_dialect_by_adapter_name(self) -> None:
        assert dialect_for("pytest") is DIALECT

    def test_pytest_report_normalises_into_the_shared_schema(self) -> None:
        report = parse_report(FIXTURES / "dialect-mixed.xml", DIALECT)
        cases = {case.id: case for case in report.cases}
        assert sorted(cases) == [
            "tests/test_basket.py::TestPricing::test_price[2-4]",
            "tests/test_basket.py::test_flaky_total",
            "tests/test_basket.py::test_receipt",
            "tests/test_basket.py::test_rounding",
            "tests/test_basket.py::test_total",
            "tests/test_basket.py::test_vendor",
        ]
        vendor = cases["tests/test_basket.py::test_vendor"]
        assert (vendor.outcome, vendor.reason) == ("skipped", "waiting on vendor fix")
        rounding = cases["tests/test_basket.py::test_rounding"]
        assert (rounding.outcome, rounding.reason) == ("xfail", "known defect")
        receipt = cases["tests/test_basket.py::test_receipt"]
        assert receipt.outcome == "error"
        assert "TEST.fast-tier-isolation" in receipt.reason
        assert cases["tests/test_basket.py::test_flaky_total"].retries == 1
        assert report.properties["omp.tier"] == "t1"
        assert report.properties["omp.runtime"] == "python 3.11.2"
        assert report.wall_s == 0.512

    def test_subtests_the_suite_counts_are_not_read_as_a_truncated_report(
        self,
    ) -> None:
        tallied = subtest_report(
            '<property name="omp.subtests.tests" value="7"/>'
            '<property name="omp.subtests.failures" value="1"/>'
            '<property name="omp.subtests.errors" value="0"/>'
        )
        assert tallied.declared == {"tests": 2, "failures": 0, "errors": 0}
        assert propagation.truncation_findings(tallied) == []
        untallied = subtest_report("")
        assert [
            item.message for item in propagation.truncation_findings(untallied)
        ] == [
            "suites declare 1 failures but the report holds 0",
            "suites declare 9 tests but the report holds 2",
        ]

    def test_guard_evidence_accepts_a_fully_guarded_fast_tier(self) -> None:
        assert adapter.guard_evidence(FIXTURES / "guards-t1-armed.xml", "t1") == []

    def test_guard_evidence_reports_a_missing_fast_tier_guard(self) -> None:
        findings = adapter.guard_evidence(
            FIXTURES / "guards-t1-user-data-only.xml", "t1"
        )
        assert [item.rule_id for item in findings] == ["TEST.fast-tier-isolation"]

    def test_guard_evidence_rejects_another_tiers_report(self) -> None:
        findings = adapter.guard_evidence(FIXTURES / "guards-t1-armed.xml", "t2")
        assert [item.rule_id for item in findings] == ["TEST.failure-propagation"]

    def test_user_data_guard_protects_home_but_not_allowed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            home = Path(scratch, "home")
            project = home / "project"
            self.addCleanup(guards.configure, None, [])
            guards.configure(str(home), [str(project)])
            assert guards.is_protected(str(home / ".config/app.json"))
            assert not guards.is_protected(str(project / "out.txt"))
            assert not guards.is_protected(str(Path(scratch, "elsewhere.txt")))
            write = guards.user_data_violation("open", (str(home / "a"), "w", 0))
            read = guards.user_data_violation("open", (str(home / "a"), "r", 0))
            assert write is not None
            assert "TEST.user-data-guard" in write
            assert read is None

    def test_fast_tier_guard_names_process_and_network_events(self) -> None:
        process = guards.fast_tier_violation(
            "subprocess.Popen", ("git", ["git"], None, None)
        )
        assert process is not None
        assert "TEST.fast-tier-isolation" in process
        assert guards.fast_tier_violation("open", ("x", "r", 0)) is None

    def test_fast_tier_guard_blocks_host_name_resolution(self) -> None:
        for event, args in (
            ("socket.getaddrinfo", ("example.com", 443, 0, 0, 0)),
            ("socket.gethostbyname", ("example.com",)),
            ("socket.gethostbyaddr", ("192.0.2.1",)),
            ("socket.getnameinfo", (("192.0.2.1", 443), 0)),
        ):
            reason = guards.fast_tier_violation(event, args)
            assert reason is not None, event
            assert "TEST.fast-tier-isolation" in reason

    def test_rules_load_with_catalogue_ids_and_known_scopes(self) -> None:
        rules = adapter_lint_rules()
        assert {rule["id"] for rule in rules} == RULE_IDS
        assert all(rule["scope"] in {"tests", "production"} for rule in rules)


class TestOnlySymbolCase(unittest.TestCase):
    PRODUCTION = {
        "shop/cart.py": (
            "def total(items):\n    return sum(items)\n\n\n"
            "def reset_cache_for_tests():\n    pass\n\n\n"
            "def _private():\n    pass\n\n\nclass Basket:\n    pass\n"
        ),
        "shop/cli.py": "from shop.cart import total\n\n\ndef main():\n    print(total([1]))\n",
    }

    TESTS = {
        "tests/test_cart.py": (
            "from shop.cart import Basket, _private, reset_cache_for_tests, total\n"
            "from shop.cli import main\n"
        )
    }

    PROFILE = {"tests": {"public_entry_points": {"shop": "shop.cli:main"}}}

    def findings(
        self,
        production: dict[str, str] | None = None,
        profile: dict[str, Any] | None = None,
    ) -> list[Finding]:
        production = production or self.PRODUCTION
        return symbol_usage.test_only_findings(
            production, self.TESTS, profile or self.PROFILE, set(production)
        )


class TestOnlySymbolTests(TestOnlySymbolCase):
    def test_symbol_only_tests_use_is_reported(self) -> None:
        found = self.findings()
        assert sorted((f.rule_id, f.path, f.message.split("'")[1]) for f in found) == [
            ("TEST.no-test-hooks", "shop/cart.py", "Basket"),
            ("TEST.no-test-hooks", "shop/cart.py", "reset_cache_for_tests"),
        ]
        assert "(tests/test_cart.py)" in found[0].message

    def test_production_use_entry_points_and_exports_pass(self) -> None:
        production = {
            **self.PRODUCTION,
            "shop/__init__.py": (
                "from shop.cart import Basket\n__all__ = ['reset_cache_for_tests']\n"
            ),
        }
        assert self.findings(production) == []

    def test_symbol_nothing_uses_is_left_to_other_rules(self) -> None:
        production = {"shop/cart.py": "def unused():\n    pass\n"}
        assert self.findings(production) == []


def adapter_lint_rules() -> list[dict[str, Any]]:
    folder = REFERENCE / "guardrails/commands/adapters/pytest/rules"
    return [json.loads(path.read_text()) for path in sorted(folder.glob("*.yml"))]


class ExtraTests(unittest.TestCase):
    def test_static_collection_reads_testcase_classes_of_any_name(self) -> None:
        collected = collect_cases("tests/test_shop.py", UNITTEST_SHOP_TEST)
        assert set(collected.behaviours) == {
            "tests/test_shop.py::FastPrices::test_price_doubles",
            "tests/test_shop.py::SlowPrices::test_price_of_zero",
        }


SUBTEST_REPORT = """\
<testsuites><testsuite tests="9" failures="1" errors="0">
<properties>{properties}</properties>
<testcase classname="tests.test_shop.Prices" name="test_rows" file="tests/test_shop.py"/>
<testcase classname="tests.test_shop.Prices" name="test_total" file="tests/test_shop.py"/>
</testsuite></testsuites>
"""


def subtest_report(properties: str) -> Any:
    text = SUBTEST_REPORT.format(properties=properties)
    return DIALECT.complete(Path("report.xml"), parse_text(text, DIALECT, "report.xml"))


UNITTEST_SHOP_TEST = """\
import unittest

import pytest

from shop.core import price

@pytest.mark.fast
class FastPrices(unittest.TestCase):
    def test_price_doubles(self):
        self.assertEqual(price(3), 6)

class PriceCase(unittest.TestCase):
    pytestmark = pytest.mark.slow

class SlowPrices(PriceCase):
    def test_price_of_zero(self):
        self.assertEqual(price(0), 0)
"""

if __name__ == "__main__":
    unittest.main()
