from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from guardrails.host.process import run_bytes, run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest
from test_reference_adapter_pytest import (
    REFERENCE,
    UNITTEST_SHOP_TEST,
    TestOnlySymbolCase,
    run_cli,
    write_profile,
)

from guardrails.commands.adapters import pytest as adapter
from guardrails.commands.adapters.pytest.dialect import DIALECT
from guardrails.commands.adapters.pytest.invocation import tier_arguments
from guardrails.commands.test_evidence import propagation
from guardrails.toolkit.test_report.collection import read_ids
from guardrails.toolkit.test_report.junit import parse_report

pytestmark = pytest.mark.seam


MUTATION_LOCK = REFERENCE / "requirements-mutation.lock"


def git(repo: Path, *args: str) -> None:
    run_bytes(
        ["git", "-C", str(repo), *args],
        check=True,
        timeout=60,
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )


class StandardLibraryTests(unittest.TestCase):
    def test_adapter_modules_import_without_the_extra(self) -> None:
        code = (
            "import sys\n"
            f"sys.path.insert(0, {str(REFERENCE)!r})\n"
            "import guardrails.commands.adapters.pytest, guardrails.commands.adapters.pytest.dialect\n"
            "import guardrails.commands.adapters.pytest.guards, guardrails.commands.adapters.pytest.tiers\n"
            "import guardrails.commands.adapters.pytest.lint\n"
            "assert 'pytest' not in sys.modules and 'ast_grep_py' not in sys.modules\n"
        )
        result = run_text([sys.executable, "-I", "-c", code], timeout=60)
        assert result.returncode == 0, result.stderr


GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
}


class TestOnlySymbolTests(TestOnlySymbolCase):
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
    def test_every_conformance_case_passes(self) -> None:
        from guardrails.commands.adapters.pytest.conformance import (
            load_cases,
            run_conformance,
        )

        results = run_conformance()
        assert len(results) == len(load_cases())
        for result in results:
            with self.subTest(case=result.case_id):
                assert result.passed, result.detail

    def test_conformance_runs_ignore_the_callers_framework_variables(self) -> None:
        from guardrails.commands.adapters.pytest.conformance import run_conformance

        caller = {
            "PYTEST_PLUGINS": "absent_plugin_module",
            "PYTEST_ADDOPTS": "--absent-option",
        }
        with mock.patch.dict(os.environ, caller):
            (result,) = run_conformance(["fast-tier-clean"])
        assert result.passed, result.detail

    def test_the_conformance_module_reports_each_case_and_exits_zero(self) -> None:
        result = run_text(
            [
                sys.executable,
                "-m",
                "guardrails.commands.adapters.pytest.conformance",
                "fast-tier-clean",
            ],
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines() == [
            "pass fast-tier-clean (TEST.fast-tier-isolation)",
            "1/1 conformance cases pass",
        ]

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

    def test_cli_lint_drops_findings_under_the_record_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            source = SHOP_TEST + (
                "\n\ndef test_patched(monkeypatch):\n"
                "    import shop.core\n"
                "    monkeypatch.setattr('shop.core.price', lambda q: 0)\n"
            )
            repo = shop_repository(Path(scratch), source)
            excluded = {
                **SHOP_PROFILE,
                "guardrails": {"check": {"exclude": ["tests/**"]}},
            }
            write_profile(repo, excluded)
            code, out = run_cli("adapter", "--repo", str(repo), "pytest", "lint")
            assert "TEST.no-self-mocking" not in out
            assert "shop/core.py: TEST.no-test-hooks" in out

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
            assert code == 1
            assert "TEST.tier-selectors" in out
            assert "selected by tiers t1, t2" in out

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
                *tier_arguments("t1", str(report)),
                "tests",
            ]
            environment = {**os.environ, "PYTHONPATH": str(REFERENCE)}
            environment.pop("PYTEST_ADDOPTS", None)
            result = run_text(command, cwd=repo, env=environment, timeout=300)
            assert result.returncode == 0, result.stdout + result.stderr
            head = run_text(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=60
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

    def test_parallel_workers_guard_the_real_home_of_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            home, temporary, project = root / "home", root / "tmp", root / "project"
            for folder in (home, temporary, project / "tests"):
                folder.mkdir(parents=True)
            (project / "tests/test_home.py").write_text(
                "import os\nfrom pathlib import Path\n\n\n"
                "def test_writes_the_real_home():\n"
                '    Path(os.environ["REAL_HOME"], "note.txt").write_text("x")\n',
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "PYTHONPATH": str(REFERENCE),
                "HOME": str(home),
                "TMPDIR": str(temporary),
                "REAL_HOME": str(home),
            }
            environment.pop("PYTEST_ADDOPTS", None)
            result = run_text(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-p",
                    "guardrails.commands.adapters.pytest.plugin",
                    "--omp-guard=user-data",
                    "-n",
                    "2",
                    "tests",
                ],
                cwd=project,
                env=environment,
                timeout=300,
            )
            assert result.returncode == 1, result.stdout + result.stderr
            assert "TEST.user-data-guard" in result.stdout
            assert not (home / "note.txt").exists()

    def test_a_clean_run_with_unittest_subtests_passes_report_check(self) -> None:
        with tempfile.TemporaryDirectory() as scratch:
            repo = shop_repository(Path(scratch), UNITTEST_SUBTEST_TEST)
            report = Path(scratch, "t1.xml")
            environment = {**os.environ, "PYTHONPATH": str(REFERENCE)}
            environment.pop("PYTEST_ADDOPTS", None)
            result = run_text(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-m",
                    "fast",
                    *tier_arguments("t1", str(report)),
                    "tests",
                ],
                cwd=repo,
                env=environment,
                timeout=300,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            parsed = parse_report(report, DIALECT)
            assert propagation.evaluate(parsed, SHOP_PROFILE, None) == []
            assert parsed.properties["omp.subtests.tests"] == "3"

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
            result = run_text(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    "-m",
                    "fast",
                    *tier_arguments("t1", str(report)),
                    "tests",
                ],
                cwd=repo,
                env=environment,
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
