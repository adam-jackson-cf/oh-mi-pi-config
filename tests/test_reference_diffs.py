"""TEST.coverage-ratchet, TEST.golden-change-record and TEST.identity-artefacts over git diffs."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import (
    approval,
    config,
    exception as agentrails_exception,
    write_config,
)

from guardrails.commands import cli
from guardrails.commands.adapters import affected
from guardrails.commands.diffs import (
    coverage_ratchet,
    golden_record,
    growth,
    identity_artefacts,
    timeouts,
)

pytestmark = pytest.mark.seam

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/agent-rails/engine"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Fixture",
    "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "Fixture",
    "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


class FixtureRepo:
    """A throwaway git repository whose commits are made from file dictionaries."""

    def __init__(self, test: unittest.TestCase) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-diffs-")
        test.addCleanup(scratch.cleanup)
        self.path = Path(scratch.name).resolve() / "repo"
        self.path.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_ENV)
        return run_text(
            ["git", "-C", str(self.path), *args], check=True, env=env, timeout=60
        ).stdout.strip()

    def commit(
        self, files: Mapping[str, str | None], profile: dict[str, Any] | None = None
    ) -> str:
        """Commit ``files``; ``profile`` sections go to the git-excluded repo config."""
        if profile is not None:
            write_config(self.path, config(**profile))
        for name, text in files.items():
            target = self.path / name
            if text is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "fixture")
        return self.git("rev-parse", "HEAD")


def rules(findings: list[Any]) -> list[tuple[str, str]]:
    return sorted((finding.rule_id, finding.path) for finding in findings)


BASE_PROFILE: dict[str, Any] = {
    "tests": {
        "coverage": {"line_floor": 90, "branch_floor": 80},
        "layout": {
            "support_dir": "tests/support",
            "fixtures_dir": "tests/fixtures",
            "test_file_patterns": ["test_*.py"],
        },
        "adapters": [
            {
                "name": "pytest",
                "tiers": {
                    "t1": {"location": "tests/unit", "selector": "fast"},
                    "t2": {"location": "tests/journeys", "selector": "journey"},
                    "t3": {"location": "tests/journeys", "selector": "scheduled"},
                },
            }
        ],
        "identity_artefacts": {"migrations/*.sql": "never edit an applied migration"},
    },
    "checkers": {
        "golden_record": {
            "globs": ["tests/fixtures/golden/*"],
            "change_record": "docs/golden-changes.md",
        },
        "identity_artefacts": {"non_production": ["docs/*"]},
    },
}


class CoverageRatchetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(
            {
                "src/app.py": "def run():\n    return 1\n",
                ".coveragerc": "[run]\nomit =\n    build/*\n[report]\nfail_under = 90\n",
                "package.json": '{\n  "jest": {\n    "coveragePathIgnorePatterns": [\n'
                '      "dist/"\n    ],\n    "coverageThreshold": {"global": {"lines": 85}}\n'
                "  }\n}\n",
            },
            BASE_PROFILE,
        )

    def check(self, profile: dict[str, Any] = BASE_PROFILE) -> list[Any]:
        return coverage_ratchet.run(self.repo.path, profile, ["--base", self.base])

    def test_unchanged_coverage_passes(self) -> None:
        self.repo.commit({"src/app.py": "def run():\n    return 2\n"})
        assert self.check() == []

    def test_added_pragma_is_reported(self) -> None:
        self.repo.commit({
            "src/app.py": "def run():  # pragma: no cover\n    return 1\n"
        })
        findings = self.check()
        assert rules(findings) == [("TEST.coverage-ratchet", "src/app.py")]
        assert "pragma" in findings[0].message

    def test_istanbul_ignore_is_reported(self) -> None:
        self.repo.commit({
            "src/view.ts": "/* istanbul ignore next */\nexport const x = 1;\n"
        })
        assert rules(self.check()) == [("TEST.coverage-ratchet", "src/view.ts")]

    def test_moved_pragma_line_is_not_new(self) -> None:
        base = self.repo.commit({"src/a.py": "x = 1\ny = 2  # pragma: no cover\n"})
        self.repo.commit({"src/a.py": "y = 2  # pragma: no cover\nx = 1\n"})
        assert (
            coverage_ratchet.run(self.repo.path, BASE_PROFILE, ["--base", base]) == []
        )

    def test_lowered_and_removed_floors_are_reported(self) -> None:
        self.repo.commit({
            ".coveragerc": "[run]\nomit =\n    build/*\n[report]\nfail_under = 70\n"
        })
        findings = self.check()
        assert rules(findings) == [("TEST.coverage-ratchet", ".coveragerc")]
        assert "lowered from 90 to 70" in findings[0].message
        self.repo.commit({".coveragerc": "[run]\nomit =\n    build/*\n"})
        assert "was removed" in self.check()[0].message

    def test_deleted_config_file_removes_its_floors(self) -> None:
        self.repo.commit({".coveragerc": None})
        findings = self.check()
        assert rules(findings) == [("TEST.coverage-ratchet", ".coveragerc")]
        assert "coverage floor 'fail_under' 90 was removed" in findings[0].message

    def test_deleted_config_file_with_new_approval_passes(self) -> None:
        approved = json.loads(json.dumps(BASE_PROFILE))
        approved["approvals"] = [approval("ratchet", ".coveragerc", "ADR-13")]
        self.repo.commit({".coveragerc": None}, approved)
        assert self.check(approved) == []

    def test_config_renamed_to_a_non_config_name_removes_its_floors(self) -> None:
        text = (self.repo.path / ".coveragerc").read_text()
        self.repo.commit({".coveragerc": None, "notes/old-coveragerc.txt": text})
        findings = self.check()
        assert rules(findings) == [("TEST.coverage-ratchet", ".coveragerc")]
        assert "was removed" in findings[0].message

    def test_config_renamed_to_another_config_name_keeps_its_floors(self) -> None:
        text = (self.repo.path / ".coveragerc").read_text()
        self.repo.commit({".coveragerc": None, "tox.ini": text})
        assert self.check() == []

    def test_raised_floor_passes(self) -> None:
        self.repo.commit({
            ".coveragerc": "[run]\nomit =\n    build/*\n[report]\nfail_under = 95\n"
        })
        assert self.check() == []

    def test_new_exclusions_are_reported_in_ini_and_json(self) -> None:
        self.repo.commit({
            ".coveragerc": "[run]\nomit =\n    build/*\n    src/slow.py\n"
            "[report]\nfail_under = 90\n",
            "package.json": '{\n  "jest": {\n    "coveragePathIgnorePatterns": [\n'
            '      "dist/",\n      "src/hard/"\n    ],\n'
            '    "coverageThreshold": {"global": {"lines": 85}}\n  }\n}\n',
        })
        findings = self.check()
        assert rules(findings) == [
            ("TEST.coverage-ratchet", ".coveragerc"),
            ("TEST.coverage-ratchet", "package.json"),
        ]
        assert all("exclusion" in finding.message for finding in findings)

    def test_record_floors_are_owner_config_outside_the_diff(self) -> None:
        lowered = json.loads(json.dumps(BASE_PROFILE))
        lowered["tests"]["coverage"]["branch_floor"] = 60
        self.repo.commit({}, lowered)
        assert self.check(lowered) == []

    def test_record_approval_clears_the_finding(self) -> None:
        approved = json.loads(json.dumps(BASE_PROFILE))
        approved["approvals"] = [approval("ratchet", "src/app.py", "ADR-12")]
        self.repo.commit(
            {"src/app.py": "def run():  # pragma: no cover\n    return 1\n"}, approved
        )
        assert self.check(approved) == []

    def test_approval_for_another_path_does_not_count(self) -> None:
        approved = json.loads(json.dumps(BASE_PROFILE))
        approved["approvals"] = [approval("ratchet", "lib/*", "ADR-12")]
        self.repo.commit({
            "src/app.py": "def run():  # pragma: no cover\n    return 1\n"
        })
        findings = self.check(approved)
        assert rules(findings) == [("TEST.coverage-ratchet", "src/app.py")]

    def test_placeholder_approval_does_not_count(self) -> None:
        approved = json.loads(json.dumps(BASE_PROFILE))
        approved["checkers"]["coverage_ratchet"] = {
            "approvals": [{"path": "src/app.py", "approval": "<approval reference>"}]
        }
        self.repo.commit(
            {"src/app.py": "def run():  # pragma: no cover\n    return 1\n"}, approved
        )
        assert len(self.check(approved)) == 1

    def test_shared_config_counts_only_its_coverage_sections(self) -> None:
        base = self.repo.commit({
            "pyproject.toml": '[tool.ruff]\nexclude = [\n  "build",\n]\n'
            '[tool.mypy]\nexclude = ["build"]\n[tool.other]\nlines = 90\n'
            '[tool.coverage.run]\nomit = [\n  "build/*",\n]\n'
            "[tool.coverage.report]\nfail_under = 90\n"
        })
        self.repo.commit({
            "pyproject.toml": '[tool.ruff]\nexclude = [\n  "build",\n  "vendor",\n]\n'
            '[tool.mypy]\nexclude = ["build", "vendor"]\n[tool.other]\nlines = 10\n'
            '[tool.coverage.run]\nomit = [\n  "build/*",\n  "src/slow.py",\n]\n'
            "[tool.coverage.report]\nfail_under = 80\n"
        })
        findings = coverage_ratchet.run(self.repo.path, BASE_PROFILE, ["--base", base])
        assert sorted(finding.message for finding in findings) == [
            "coverage floor 'fail_under' lowered from 90 to 80",
            'line 13 adds a coverage exclusion: "src/slow.py",',
        ]
        assert rules(findings) == [("TEST.coverage-ratchet", "pyproject.toml")] * 2

    def test_missing_base_is_a_finding(self) -> None:
        findings = coverage_ratchet.run(self.repo.path, BASE_PROFILE, [])
        assert rules(findings) == [("TEST.coverage-ratchet", ".")]
        assert "--base" in findings[0].message


class GoldenRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(
            {
                "tests/fixtures/golden/report.txt": "total 3\n",
                "docs/golden-changes.md": "# Golden changes\n",
            },
            BASE_PROFILE,
        )

    def check(self, profile: dict[str, Any] = BASE_PROFILE) -> list[Any]:
        return golden_record.run(self.repo.path, profile, ["--base", self.base])

    def test_changed_golden_without_record_is_reported(self) -> None:
        self.repo.commit({"tests/fixtures/golden/report.txt": "total 4\n"})
        assert rules(self.check()) == [
            ("TEST.golden-change-record", "tests/fixtures/golden/report.txt")
        ]

    def test_record_naming_the_golden_clears_it(self) -> None:
        self.repo.commit({
            "tests/fixtures/golden/report.txt": "total 4\n",
            "docs/golden-changes.md": "# Golden changes\n\n- tests/fixtures/golden/report.txt:"
            " totals now include refunds\n",
        })
        assert self.check() == []

    def test_record_naming_another_file_does_not_count(self) -> None:
        self.repo.commit({
            "tests/fixtures/golden/report.txt": "total 4\n",
            "docs/golden-changes.md": "# Golden changes\n\n- other.txt: reason\n",
        })
        assert len(self.check()) == 1

    def test_deleted_golden_needs_a_record_and_new_golden_does_not(self) -> None:
        self.repo.commit({
            "tests/fixtures/golden/report.txt": None,
            "tests/fixtures/golden/new.txt": "x\n",
        })
        assert rules(self.check()) == [
            ("TEST.golden-change-record", "tests/fixtures/golden/report.txt")
        ]

    def test_no_golden_globs_means_nothing_to_check(self) -> None:
        self.repo.commit({"tests/fixtures/golden/report.txt": "total 4\n"})
        assert golden_record.run(self.repo.path, {}, ["--base", self.base]) == []

    def test_globs_without_change_record_is_a_profile_finding(self) -> None:
        profile = {
            "checkers": {"golden_record": {"globs": ["tests/fixtures/golden/*"]}}
        }
        findings = golden_record.run(self.repo.path, profile, ["--base", self.base])
        assert rules(findings) == [
            ("TEST.golden-change-record", ".agentrails/config.json#checkers")
        ]


class IdentityArtefactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(
            {
                "migrations/001_init.sql": "create table t (id int);\n",
                "src/app.py": "x = 1\n",
                "tests/unit/test_app.py": "def test_x():\n    pass\n",
            },
            BASE_PROFILE,
        )

    def check(self, profile: dict[str, Any] = BASE_PROFILE) -> list[Any]:
        return identity_artefacts.run(self.repo.path, profile, ["--base", self.base])

    def test_artefact_change_in_test_only_work_is_reported(self) -> None:
        self.repo.commit({
            "migrations/001_init.sql": "create table t (id bigint);\n",
            "tests/unit/test_app.py": "def test_x():\n    assert True\n",
            "docs/notes.md": "notes\n",
        })
        findings = self.check()
        assert rules(findings) == [
            ("TEST.identity-artefacts", "migrations/001_init.sql")
        ]
        assert "never edit an applied migration" in findings[0].message

    def test_artefact_change_with_production_change_passes(self) -> None:
        self.repo.commit({
            "migrations/001_init.sql": "create table t (id bigint);\n",
            "src/app.py": "x = 2\n",
        })
        assert self.check() == []

    def test_test_only_work_without_artefacts_passes(self) -> None:
        self.repo.commit({"tests/unit/test_app.py": "def test_x():\n    assert 1\n"})
        assert self.check() == []

    def test_approved_exemption_disables_the_rule(self) -> None:
        exempt = {
            **BASE_PROFILE,
            "tests": {
                **BASE_PROFILE.get("tests", {}),
                "identity_artefacts": {"none": True},
            },
            "approvals": [
                approval("exemption", "identity_artefacts", "no ids (ADR-3)")
            ],
        }
        self.repo.commit({"migrations/001_init.sql": "changed\n"})
        assert self.check(exempt) == []


class TimeoutIncreaseTests(unittest.TestCase):
    """TEST.no-evasion: a changed test never raises a timeout literal."""

    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(
            {
                "tests/unit/test_sync.py": (
                    "import subprocess\n\n\ndef test_sync():\n"
                    "    subprocess.run(['sync'], timeout=5, check=True)\n"
                ),
                "tests/unit/sync.test.ts": "it('syncs', async () => {}, { timeout: 2000 });\n",
                "src/app.py": "TIMEOUT_S = 5\nrequest(timeout=5)\n",
            },
            BASE_PROFILE,
        )

    def check(self, profile: dict[str, Any] = BASE_PROFILE) -> list[Any]:
        return timeouts.run(self.repo.path, profile, ["--base", self.base])

    def test_raised_timeout_in_a_changed_test_is_reported(self) -> None:
        self.repo.commit({
            "tests/unit/test_sync.py": (
                "import subprocess\n\n\ndef test_sync():\n"
                "    subprocess.run(['sync'], timeout=30, check=True)\n"
            )
        })
        findings = self.check()
        assert rules(findings) == [("TEST.no-evasion", "tests/unit/test_sync.py")]
        assert "raises timeout 'timeout' from 5 to 30" in findings[0].message

    def test_raised_object_style_timeout_is_reported(self) -> None:
        layout = {
            **BASE_PROFILE["tests"]["layout"],
            "test_file_patterns": ["*.test.ts"],
        }
        profile = {
            **BASE_PROFILE,
            "tests": {**BASE_PROFILE.get("tests", {}), "layout": layout},
        }
        self.repo.commit({
            "tests/unit/sync.test.ts": "it('syncs', async () => {}, { timeout: 9000 });\n"
        })
        assert rules(self.check(profile)) == [
            ("TEST.no-evasion", "tests/unit/sync.test.ts")
        ]

    def test_lowered_new_or_production_timeouts_pass(self) -> None:
        self.repo.commit({
            "tests/unit/test_sync.py": (
                "import subprocess\n\n\ndef test_sync():\n"
                "    subprocess.run(['sync'], timeout=2, check=True)\n\n\n"
                "def test_other():\n    subprocess.run(['true'], timeout=60, check=True)\n"
            ),
            "src/app.py": "TIMEOUT_S = 50\nrequest(timeout=50)\n",
        })
        assert self.check() == []

    def test_reason_backed_exception_waives_the_increase(self) -> None:
        self.repo.commit({
            "tests/unit/test_sync.py": (
                "import subprocess\n\n\ndef test_sync():\n"
                "    subprocess.run(['sync'], timeout=30, check=True)\n"
            )
        })
        exception = agentrails_exception(
            "TEST.no-evasion",
            "tests/unit/test_sync.py",
            "Owner-approved external sync deadline",
        )
        waived = {**BASE_PROFILE, "exceptions": [exception]}
        assert self.check(waived) == []


AFFECTED_PROFILE: dict[str, Any] = {
    **BASE_PROFILE,
    "tests": {
        **BASE_PROFILE.get("tests", {}),
        "adapters": [
            {
                "name": "pytest",
                "tiers": {
                    "t1": {"location": "tests/unit", "selector": "fast"},
                    "t2": {"location": "tests/boundary", "selector": "boundary"},
                    "t3": {"location": "tests/boundary", "selector": "scheduled"},
                },
            }
        ],
        "coverage": {"scope": ["src/shop"], "line_floor": 90, "branch_floor": 80},
        "verification_surface": {"journey_file_pattern": "journeys/*.journey.py"},
    },
    "structure": {
        "owners": {
            "files": {"src/shop/pricing.py": "billing"},
            "directories": {"src/shop/": "cart", "src/shop/billing/": "billing"},
        },
        "ownership_areas": {
            "map": {
                "tests/boundary/test_cart_*.py": "cart",
                "tests/boundary/test_billing_*.py": "billing",
                "journeys/checkout.journey.py": "cart",
                "journeys/invoice.journey.py": "billing",
            }
        },
    },
}
AFFECTED_FILES = {
    "src/shop/cart.py": "x = 1\n",
    "src/shop/pricing.py": "x = 1\n",
    "src/shop/billing/invoice.py": "x = 1\n",
    "tests/boundary/test_cart_flow.py": "def test_a():\n    pass\n",
    "tests/boundary/test_billing_flow.py": "def test_b():\n    pass\n",
    "tests/unit/test_cart.py": "def test_c():\n    pass\n",
    "journeys/checkout.journey.py": "x = 1\n",
    "journeys/invoice.journey.py": "x = 1\n",
    "docs/readme.md": "docs\n",
}


class AffectedSelectionTests(unittest.TestCase):
    """Affected T2 from Ownership areas: seams and journeys of the changed areas."""

    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(AFFECTED_FILES, AFFECTED_PROFILE)

    def select(self, profile: dict[str, Any] = AFFECTED_PROFILE) -> affected.Selection:
        return affected.affected(self.repo.path, profile, ["--base", self.base])

    def test_change_selects_the_owning_areas_seams_and_journeys(self) -> None:
        self.repo.commit({"src/shop/billing/invoice.py": "x = 2\n"})
        assert self.select().paths == (
            "journeys/invoice.journey.py",
            "tests/boundary/test_billing_flow.py",
        )

    def test_exact_file_owner_beats_its_directory(self) -> None:
        self.repo.commit({"src/shop/pricing.py": "x = 2\n"})
        assert "tests/boundary/test_billing_flow.py" in self.select().paths
        assert "tests/boundary/test_cart_flow.py" not in self.select().paths

    def test_changed_t2_file_is_selected_and_docs_select_nothing(self) -> None:
        self.repo.commit({
            "tests/boundary/test_cart_flow.py": "def test_a():\n    pass\n\n",
            "docs/readme.md": "x\n",
        })
        assert self.select().paths == ("tests/boundary/test_cart_flow.py",)

    def test_unowned_production_change_selects_all_t2(self) -> None:
        profile = json.loads(json.dumps(AFFECTED_PROFILE))
        profile["structure"]["owners"]["directories"] = {"src/shop/billing/": "billing"}
        self.repo.commit({"src/shop/cart.py": "x = 2\n"})
        selection = self.select(profile)
        assert len(selection.paths) == 4
        assert "src/shop/cart.py" in selection.reason

    def test_cli_and_adapter_print_the_selection(self) -> None:
        self.repo.commit({"src/shop/cart.py": "x = 2\n"})
        location = ["--repo", str(self.repo.path)]
        args = [*location, "pytest", "affected", "--base", self.base]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["affected", *location, "--base", self.base])
            adapter_code = cli.main(["adapter", *args])
        assert (code, adapter_code) == (0, 0)
        lines = out.getvalue().splitlines()
        assert lines == [
            "journeys/checkout.journey.py",
            "tests/boundary/test_cart_flow.py",
            "-m boundary -- journeys/checkout.journey.py tests/boundary/test_cart_flow.py",
        ]


GROWTH_PROFILE = BASE_PROFILE
CART_TEST = (
    "from shop.cart import total\n\n\n"
    "def test_empty_cart_totals_zero():\n    assert total([]) == 0\n"
)
SUMMED = "def test_items_are_summed():\n{comment}    assert total([1, 2]) == 3\n"


class TestGrowthTests(unittest.TestCase):
    """TEST.growth.justified and TEST.duplicate-path through the pytest static collector."""

    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(
            {
                "src/shop/cart.py": "def total(items):\n    return 0\n",
                "tests/unit/test_cart.py": CART_TEST,
            },
            GROWTH_PROFILE,
        )

    def check(self, profile: dict[str, Any] = GROWTH_PROFILE) -> list[Any]:
        return growth.run(self.repo.path, profile, ["--base", self.base])

    def add_test(self, source: str) -> list[Any]:
        self.repo.commit({"tests/unit/test_cart.py": CART_TEST + "\n\n" + source})
        return self.check()

    def test_new_descriptive_behavior_name_passes_without_comments(self) -> None:
        assert self.add_test(SUMMED.format(comment="")) == []

    def test_placeholder_or_generic_names_are_reported(self) -> None:
        for name in ("test_case", "test_TBD", "test_123", "test_total"):
            with self.subTest(name=name):
                self.setUp()
                source = SUMMED.format(comment="").replace(
                    "test_items_are_summed", name
                )
                findings = self.add_test(source)
                assert rules(findings) == [
                    ("TEST.growth.justified", "tests/unit/test_cart.py")
                ]
                assert name in findings[0].message
                assert "name the test" in findings[0].fix

    def test_behavior_comments_do_not_rescue_placeholder_names(self) -> None:
        findings = self.add_test(
            "def test_case():\n    # behaviour: totals sum the items\n    assert total([1, 2]) == 3\n"
        )
        assert rules(findings) == [("TEST.growth.justified", "tests/unit/test_cart.py")]

    def test_existing_test_names_are_not_rejudged(self) -> None:
        self.repo.commit({"tests/unit/test_cart.py": CART_TEST + "\n"})
        assert self.check() == []

    def test_one_descriptive_name_covers_every_parameter_row(self) -> None:
        rows = (
            "import pytest\nfrom shop.cart import total\n\n\n"
            "class TestTotals:\n"
            "    @pytest.mark.parametrize('items, expected', [([1], 1), ([2], 2)])\n"
            "    def test_cart_totals_items(self, items, expected):\n        assert total(items) == expected\n"
            "\n"
            "    @pytest.mark.parametrize('items', [[1], [2]])\n"
            "    def test_case(self, items):\n        assert total(items) > 0\n"
        )
        self.repo.commit({"tests/unit/test_rows.py": rows})
        findings = self.check()
        assert len(findings) == 1
        assert "TestTotals::test_case has no descriptive" in findings[0].message

    def test_duplicate_call_path_and_assertion_is_reported_with_both_names(
        self,
    ) -> None:
        renamed = (
            "def test_no_items_total_zero():\n"
            "    # behaviour: an empty cart totals zero\n"
            "    assert total([]) == 0\n"
        )
        self.repo.commit({
            "tests/unit/test_cart_again.py": "from shop.cart import total\n\n\n"
            + renamed
        })
        findings = self.check()
        assert rules(findings) == [
            ("TEST.duplicate-path", "tests/unit/test_cart_again.py")
        ]
        assert (
            "tests/unit/test_cart.py::test_empty_cart_totals_zero"
            in findings[0].message
        )
        assert "merge or remove" in findings[0].fix

    def test_distinct_assertions_are_not_duplicates(self) -> None:
        source = (
            "# behaviour: one item totals itself\n"
            "def test_one_item():\n    assert total([4]) == 4\n"
        )
        assert self.add_test(source) == []

    def test_dynamic_rows_in_a_changed_test_are_reported(self) -> None:
        self.repo.commit({
            "tests/unit/test_cart.py": CART_TEST.replace(
                "def test_empty",
                "@pytest.mark.parametrize('x', rows())\ndef test_empty",
            ).replace("from shop", "import pytest\nfrom shop"),
        })
        findings = self.check()
        assert ("TEST.growth.justified", "tests/unit/test_cart.py") in rules(findings)
        assert any("cannot collect tests statically" in f.message for f in findings)

    def test_no_changed_tests_needs_no_adapter(self) -> None:
        self.repo.commit({"src/shop/cart.py": "def total(items):\n    return 1\n"})
        assert self.check({**BASE_PROFILE}) == []

    def test_changed_tests_without_a_collector_adapter_are_reported(self) -> None:
        self.repo.commit({"tests/unit/test_cart.py": CART_TEST + "\n"})
        profile = {
            **BASE_PROFILE,
            "tests": {
                **BASE_PROFILE.get("tests", {}),
                "adapters": [
                    {
                        "name": "uninstalled_adapter",
                        "tiers": {"t1": {"location": "tests"}},
                    }
                ],
            },
        }
        findings = self.check(profile)
        assert rules(findings) == [
            ("TEST.growth.justified", ".agentrails/config.json#tests")
        ]

    def test_each_changed_test_file_is_collected_by_its_own_framework(self) -> None:
        profile = {
            **BASE_PROFILE,
            "tests": {
                **BASE_PROFILE.get("tests", {}),
                "layout": {
                    **BASE_PROFILE["tests"]["layout"],
                    "test_file_patterns": ["test_*.py", "*.test.ts"],
                },
                "adapters": [
                    {"name": "pytest", "tiers": {"t1": {"location": "tests/unit"}}},
                    {"name": "bun_test", "tiers": {"t1": {"location": "tests/unit"}}},
                ],
            },
        }
        self.repo.commit({
            "tests/unit/test_cart.py": CART_TEST + "\n" + SUMMED.format(comment=""),
            "tests/unit/cart.test.ts": (
                "import { test, expect } from 'bun:test';\n"
                "test('sum', () => { expect(1 + 1).toBe(2); });\n"
            ),
        })
        findings = self.check(profile)
        assert rules(findings) == [("TEST.growth.justified", "tests/unit/cart.test.ts")]
        assert "tests/unit/cart.test.ts::sum has no descriptive" in findings[0].message


class DiffCheckCliTests(unittest.TestCase):
    def test_diff_check_runs_all_three_rules_and_exits_one(self) -> None:
        repo = FixtureRepo(self)
        base = repo.commit({"tests/fixtures/golden/report.txt": "a\n"}, BASE_PROFILE)
        repo.commit({
            "tests/fixtures/golden/report.txt": "b\n",
            "src/app.py": "x = 1  # pragma: no cover\n",
        })
        with unittest.mock.patch("sys.stdout") as stdout:
            location = ["--repo", str(repo.path)]
            code = cli.main(["diff-check", *location, "--base", base])
        printed = "".join(call.args[0] for call in stdout.write.call_args_list)
        assert code == 1
        assert "TEST.golden-change-record" in printed
        assert "TEST.coverage-ratchet" in printed


if __name__ == "__main__":
    unittest.main()
