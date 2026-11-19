"""The guardrails reference's pytest adapter: pinned extra, dialect, guards, rules, conformance: the T2 seams.

These tests start processes or drive real Git repositories, so they run in T2.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest
from test_reference_adapter_pytest import (
    FIXTURES,
    PYTEST_ONLY,
    REFERENCE,
    UNITTEST_SHOP_TEST,
    TestOnlySymbolCase,
    requirement_entries,
    run_cli,
    write_profile,
)

from guardrails.adapters import pytest as adapter
from guardrails.adapters.pytest import mutation
from guardrails.adapters.pytest.dialect import DIALECT
from guardrails.test_evidence import propagation
from guardrails.test_evidence.collection import read_ids
from guardrails.test_evidence.junit import parse_report

pytestmark = pytest.mark.seam


MUTATION_LOCK = REFERENCE / "requirements-mutation.lock"


TestMethod = TypeVar("TestMethod", bound=Callable[..., None])


EXTRA_PRESENT = all(
    importlib.util.find_spec(name) for name in ("pytest", "ast_grep_py")
)


EXTRA_REQUIRED = os.environ.get("OMP_GUARDRAILS_REQUIRE_PYTEST_EXTRA") == "1"


def needs_extra(test: TestMethod) -> TestMethod:
    """Skip without the extra, except where the gate requires it (then the test runs and fails)."""
    if EXTRA_PRESENT or EXTRA_REQUIRED:
        return test
    return unittest.skip(
        "pinned pytest extra not installed (requirements-pytest.lock)"
    )(test)


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        timeout=60,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )


class StandardLibraryTests(unittest.TestCase):
    def test_adapter_modules_import_without_the_extra(self) -> None:
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(REFERENCE)!r})\n"
            "import guardrails.adapters.pytest, guardrails.adapters.pytest.dialect\n"
            "import guardrails.adapters.pytest.guards, guardrails.adapters.pytest.tiers\n"
            "import guardrails.adapters.pytest.lint\n"
            "assert 'pytest' not in sys.modules and 'ast_grep_py' not in sys.modules\n"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr


GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


class MutationTests(unittest.TestCase):
    """TEST.mutation-sensitivity: mutmut results normalised, then judged against the diff."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve() / "repo"
        self.out = Path(scratch.name).resolve() / "out"
        self.repo.mkdir()
        self.out.mkdir()
        self.report = str(self.out / "m.json")
        write_profile(self.repo, PYTEST_ONLY)
        source = FIXTURES / "mutmut-run"
        self.core = self.repo / "src/ledger/core.py"
        self.core.parent.mkdir(parents=True)
        # The fixture holds one real mutmut 3.8.1 run: its source file and its results file.
        text = (source / "core.py.txt").read_text()
        # The base differs from the head only inside clamp's body (lines 6-10).
        self.core.write_text(text.replace("value > high", "high < value"))
        self.commit()
        self.core.write_text(text)
        self.commit()
        meta = self.repo / "mutants/src/ledger/core.py.meta"
        meta.parent.mkdir(parents=True)
        meta.write_bytes((source / "core.py.meta").read_bytes())

    def commit(self) -> None:
        environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        environment.update(GIT_ENV)
        for args in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
            subprocess.run(
                ["git", "-C", str(self.repo), *args],
                check=True,
                capture_output=True,
                env={**environment, "GIT_CONFIG_GLOBAL": os.devnull},
                timeout=60,
            )

    def test_mutmut_results_normalise_to_function_spans(self) -> None:
        code, out = run_cli(
            "adapter",
            "--repo",
            str(self.repo),
            "pytest",
            "mutation-report",
            "--out",
            self.report,
        )
        assert code == 0, out
        report = json.loads(Path(self.report).read_text())
        spans = {
            (m["id"].rsplit(".", 1)[1], m["start_line"], m["end_line"], m["status"])
            for m in report["mutants"]
        }
        assert spans == {
            ("x_total__mutmut_1", 1, 2, "killed"),
            ("x_clamp__mutmut_1", 5, 10, "survived"),
            ("x_clamp__mutmut_2", 5, 10, "survived"),
            ("xǁAccountǁdeposit__mutmut_1", 14, 15, "killed"),
        }
        assert {m["path"] for m in report["mutants"]} == {"src/ledger/core.py"}

    def test_surviving_mutant_in_changed_code_fails_the_check(self) -> None:
        run_cli(
            "adapter",
            "--repo",
            str(self.repo),
            "pytest",
            "mutation-report",
            "--out",
            self.report,
        )
        code, out = run_cli(
            "mutation-check",
            "--repo",
            str(self.repo),
            "--report",
            self.report,
            "--base",
            "HEAD~1",
        )
        assert code == 1
        assert out.count("TEST.mutation-sensitivity") == 2
        assert "x_clamp__mutmut_1 survived in changed code" in out
        assert "x_total" not in out

    def test_killed_mutants_of_changed_code_pass_the_check(self) -> None:
        meta = self.repo / "mutants/src/ledger/core.py.meta"
        data = json.loads(meta.read_text())
        data["exit_code_by_key"] = dict.fromkeys(data["exit_code_by_key"], 1)
        meta.write_text(json.dumps(data))
        run_cli(
            "adapter",
            "--repo",
            str(self.repo),
            "pytest",
            "mutation-report",
            "--out",
            self.report,
        )
        code, out = run_cli(
            "mutation-check",
            "--repo",
            str(self.repo),
            "--report",
            self.report,
            "--base",
            "HEAD~1",
        )
        assert (code, out.strip()) == (0, "mutation-check: ok")

    def test_missing_mutmut_results_are_a_finding(self) -> None:
        (self.repo / "mutants/src/ledger/core.py.meta").unlink()
        code, out = run_cli(
            "adapter",
            "--repo",
            str(self.repo),
            "pytest",
            "mutation-report",
            "--out",
            self.report,
        )
        assert code == 1
        assert "no mutmut results" in out

    def test_exit_codes_map_to_shared_statuses(self) -> None:
        assert [mutation.status(code) for code in (1, 0, 33, 36, None, 2, 34)] == [
            "killed",
            "survived",
            "no_tests",
            "timeout",
            "incomplete",
            "incomplete",
            "incomplete",
        ]

    def test_mutation_lock_pins_both_extras_with_hashes(self) -> None:
        pyproject = tomllib.loads((REFERENCE / "pyproject.toml").read_text())
        extras = pyproject["project"]["optional-dependencies"]
        entries = requirement_entries(MUTATION_LOCK.read_text())
        specs = {spec.split(";")[0].strip() for spec in entries}
        assert set(extras["mutation"]) | set(extras["pytest"]) <= specs
        assert all(entries.values()), "every locked requirement carries a hash"


class TestOnlySymbolTests(TestOnlySymbolCase):
    """TEST.no-test-hooks: a production symbol only tests use is reported."""

    @needs_extra
    def test_lint_reports_test_only_symbols_in_linted_production_files(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = Path(scratch)
            profile = {
                **self.PROFILE,
                "adapters": [
                    {"name": "pytest", "tiers": {"t1": {"location": "tests"}}}
                ],
                "layout": {"test_file_patterns": ["test_*.py"]},
            }
            write_profile(repo, profile)
            for path, text in {**self.PRODUCTION, **self.TESTS}.items():
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text(text)
            code, out = run_cli(
                "adapter", "--repo", str(repo), "pytest", "lint", "shop/cart.py"
            )
        assert code == 1
        assert "production symbol 'reset_cache_for_tests' is used only by tests" in out


SHOP_PROFILE = {
    "adapters": [
        {
            "name": "pytest",
            "tiers": {
                "t1": {"selector": "fast", "location": "tests"},
                "t2": {"selector": "slow", "location": "tests"},
            },
        }
    ],
    "public_entry_points": {"shop": "shop.core:Basket"},
    "tiers": {
        "t1": {"command": "pytest -m fast"},
        "t2": {"command": "pytest -m slow"},
    },
    "layout": {"test_file_patterns": ["test_*.py"]},
    "house_style": {"version": 1},
    "guardrails": {"exceptions": []},
}


SHOP_TEST = """\
import pytest
from shop.core import price


@pytest.mark.fast
def test_price_doubles():
    assert price(3) == 6


@pytest.mark.slow
def test_price_of_zero():
    assert price(0) == 0
"""


def shop_repository(root: Path, test_source: str = SHOP_TEST) -> Path:
    repo = root / "repo"
    (repo / "shop").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "shop/__init__.py").write_text("")
    (repo / "shop/core.py").write_text(
        "def price(quantity):\n    return quantity * 2\n"
    )
    (repo / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    fast: t1\n    slow: t2\n"
    )
    (repo / "tests/test_shop.py").write_text(test_source)
    write_profile(repo, SHOP_PROFILE)
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    return repo


class ExtraTests(unittest.TestCase):
    @needs_extra
    def test_every_conformance_case_passes(self) -> None:
        from guardrails.adapters.pytest.conformance import load_cases, run_conformance

        results = run_conformance()
        assert len(results) == len(load_cases())
        for result in results:
            with self.subTest(case=result.case_id):
                assert result.passed, result.detail

    @needs_extra
    def test_cli_lint_reports_self_mocking_in_a_repository(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            source = SHOP_TEST + (
                "\n\ndef test_patched(monkeypatch):\n"
                "    import shop.core\n"
                "    monkeypatch.setattr('shop.core.price', lambda q: 0)\n"
            )
            repo = shop_repository(Path(scratch), source)
            code, out = run_cli("adapter", "--repo", str(repo), "pytest", "lint")
            assert code == 1
            assert "tests/test_shop.py: TEST.no-self-mocking: line 17" in out

    @needs_extra
    def test_tier_listing_feeds_the_shared_collection_check(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = shop_repository(Path(scratch))
            out_dir = Path(scratch, "collections")
            code, out = run_cli(
                "adapter", "--repo", str(repo), "pytest", "tiers", "--out", str(out_dir)
            )
            assert code == 0, out
            listing = (out_dir / "t1.txt").read_text()
            assert "tests/test_shop.py::test_price_doubles" in listing
            code, out = run_cli(
                "collection-check",
                "--repo",
                str(repo),
                "--tier",
                f"t1={out_dir / 't1.txt'}",
                "--tier",
                f"t2={out_dir / 't2.txt'}",
            )
            # Both tiers select the same file, which TEST.tier-selectors forbids.
            assert code == 1
            assert "TEST.tier-selectors" in out
            assert "selected by tiers t1, t2" in out

    @needs_extra
    def test_guarded_run_writes_the_shared_run_properties(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = shop_repository(Path(scratch))
            report = Path(scratch, "t1.xml")
            command = [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "-m",
                "fast",
                *adapter.tier_arguments("t1", str(report)),
                "tests",
            ]
            environment = {**os.environ, "PYTHONPATH": str(REFERENCE)}
            environment.pop("PYTEST_ADDOPTS", None)
            result = subprocess.run(
                command,
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=60,
            ).stdout.strip()
            parsed = parse_report(report, DIALECT)
            assert parsed.properties["omp.revision"] == head
            assert parsed.properties["omp.tier"] == "t1"
            assert parsed.properties["omp.exit_code"] == "0"
            assert parsed.properties["omp.runtime"].startswith("python 3.")
            assert [case.id for case in parsed.cases] == [
                "tests/test_shop.py::test_price_doubles"
            ]
            assert adapter.guard_evidence(report, "t1") == []

    @needs_extra
    def test_a_clean_run_with_unittest_subtests_passes_report_check(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = shop_repository(Path(scratch), UNITTEST_SUBTEST_TEST)
            report = Path(scratch, "t1.xml")
            environment = {**os.environ, "PYTHONPATH": str(REFERENCE)}
            environment.pop("PYTEST_ADDOPTS", None)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-m",
                    "fast",
                    *adapter.tier_arguments("t1", str(report)),
                    "tests",
                ],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            parsed = parse_report(report, DIALECT)
            assert propagation.evaluate(parsed, SHOP_PROFILE, None) == []
            assert parsed.properties["omp.subtests.tests"] == "3"

    @needs_extra
    def test_unittest_suites_take_tier_markers_and_run_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = shop_repository(Path(scratch), UNITTEST_SHOP_TEST)
            (repo / "tests/test_shop_module.py").write_text(UNITTEST_MODULE_TEST)
            out_dir = Path(scratch, "collections")
            code, out = run_cli(
                "adapter", "--repo", str(repo), "pytest", "tiers", "--out", str(out_dir)
            )
            assert code == 0, out
            assert read_ids(out_dir / "t1.txt") == [
                "tests/test_shop.py::FastPrices::test_price_doubles"
            ]
            assert read_ids(out_dir / "t2.txt") == [
                "tests/test_shop.py::SlowPrices::test_price_of_zero",
                "tests/test_shop_module.py::ModulePrices::test_price_of_one",
            ]
            report = Path(scratch, "t1.xml")
            environment = {**os.environ, "PYTHONPATH": str(REFERENCE)}
            environment.pop("PYTEST_ADDOPTS", None)
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-m",
                    "fast",
                    *adapter.tier_arguments("t1", str(report)),
                    "tests",
                ],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            parsed = parse_report(report, DIALECT)
            assert [(case.id, case.outcome) for case in parsed.cases] == [
                ("tests/test_shop.py::FastPrices::test_price_doubles", "passed")
            ]
            assert adapter.guard_evidence(report, "t1") == []


UNITTEST_SUBTEST_TEST = """\
import unittest

import pytest

from shop.core import price

@pytest.mark.fast
class FastPrices(unittest.TestCase):
    def test_price_doubles_every_quantity(self):
        for quantity in (0, 1, 3):
            with self.subTest(quantity=quantity):
                self.assertEqual(price(quantity), quantity * 2)
"""


UNITTEST_MODULE_TEST = """\
from unittest import TestCase

import pytest

from shop.core import price

pytestmark = pytest.mark.slow


class ModulePrices(TestCase):
    def test_price_of_one(self):
        self.assertEqual(price(1), 2)
"""


if __name__ == "__main__":
    unittest.main()
