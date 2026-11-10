"""TEST.coverage-ratchet, TEST.golden-change-record and TEST.identity-artefacts over git diffs."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/skills/setup-repository-guardrails/assets/python-reference"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails import cli  # noqa: E402
from omp_guardrails.diffs import (  # noqa: E402
    affected,
    coverage_ratchet,
    golden_record,
    identity_artefacts,
    timeouts,
)

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
        self.path = Path(scratch.name)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_ENV)
        return subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        ).stdout.strip()

    def commit(self, files: dict[str, str | None], profile: dict | None = None) -> str:
        if profile is not None:
            files = {**files, ".omp/test-profile.md": profile_text(profile)}
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


def profile_text(profile: dict) -> str:
    return f"---\n{json.dumps(profile, indent=2)}\n---\n\n# Repo test profile\n"


def rules(findings: list) -> list[tuple[str, str]]:
    return sorted((finding.rule_id, finding.path) for finding in findings)


BASE_PROFILE = {
    "coverage": {"line_floor": 90, "branch_floor": 80},
    "layout": {
        "support_dir": "tests/support",
        "fixtures_dir": "tests/fixtures",
        "test_file_patterns": ["test_*.py"],
    },
    "tiers": {"t1": {"location": "tests/unit"}, "t2": {"location": "tests/journeys"}},
    "identity_artefacts": {"migrations/*.sql": "never edit an applied migration"},
    "guardrails": {
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

    def check(self, profile: dict = BASE_PROFILE) -> list:
        return coverage_ratchet.run(self.repo.path, profile, ["--base", self.base])

    def test_unchanged_coverage_passes(self) -> None:
        self.repo.commit({"src/app.py": "def run():\n    return 2\n"})
        self.assertEqual(self.check(), [])

    def test_added_pragma_is_reported(self) -> None:
        self.repo.commit({"src/app.py": "def run():  # pragma: no cover\n    return 1\n"})
        findings = self.check()
        self.assertEqual(rules(findings), [("TEST.coverage-ratchet", "src/app.py")])
        self.assertIn("pragma", findings[0].message)

    def test_istanbul_ignore_is_reported(self) -> None:
        self.repo.commit({"src/view.ts": "/* istanbul ignore next */\nexport const x = 1;\n"})
        self.assertEqual(rules(self.check()), [("TEST.coverage-ratchet", "src/view.ts")])

    def test_moved_pragma_line_is_not_new(self) -> None:
        base = self.repo.commit({"src/a.py": "x = 1\ny = 2  # pragma: no cover\n"})
        self.repo.commit({"src/a.py": "y = 2  # pragma: no cover\nx = 1\n"})
        self.assertEqual(coverage_ratchet.run(self.repo.path, BASE_PROFILE, ["--base", base]), [])

    def test_lowered_and_removed_floors_are_reported(self) -> None:
        self.repo.commit({".coveragerc": "[run]\nomit =\n    build/*\n[report]\nfail_under = 70\n"})
        findings = self.check()
        self.assertEqual(rules(findings), [("TEST.coverage-ratchet", ".coveragerc")])
        self.assertIn("lowered from 90 to 70", findings[0].message)
        self.repo.commit({".coveragerc": "[run]\nomit =\n    build/*\n"})
        self.assertIn("was removed", self.check()[0].message)

    def test_raised_floor_passes(self) -> None:
        self.repo.commit({".coveragerc": "[run]\nomit =\n    build/*\n[report]\nfail_under = 95\n"})
        self.assertEqual(self.check(), [])

    def test_new_exclusions_are_reported_in_ini_and_json(self) -> None:
        self.repo.commit(
            {
                ".coveragerc": "[run]\nomit =\n    build/*\n    src/slow.py\n"
                "[report]\nfail_under = 90\n",
                "package.json": '{\n  "jest": {\n    "coveragePathIgnorePatterns": [\n'
                '      "dist/",\n      "src/hard/"\n    ],\n'
                '    "coverageThreshold": {"global": {"lines": 85}}\n  }\n}\n',
            }
        )
        findings = self.check()
        self.assertEqual(
            rules(findings),
            [("TEST.coverage-ratchet", ".coveragerc"), ("TEST.coverage-ratchet", "package.json")],
        )
        self.assertTrue(all("exclusion" in finding.message for finding in findings))

    def test_lowered_profile_floor_is_reported(self) -> None:
        lowered = json.loads(json.dumps(BASE_PROFILE))
        lowered["coverage"]["branch_floor"] = 60
        self.repo.commit({}, lowered)
        findings = self.check(lowered)
        self.assertEqual(rules(findings), [("TEST.coverage-ratchet", ".omp/test-profile.md")])
        self.assertIn("branch_floor lowered from 80 to 60", findings[0].message)

    def test_approval_added_in_the_same_diff_clears_the_finding(self) -> None:
        approved = json.loads(json.dumps(BASE_PROFILE))
        approved["guardrails"]["coverage_ratchet"] = {
            "approvals": [{"path": "src/app.py", "approval": "ADR-12"}]
        }
        self.repo.commit({"src/app.py": "def run():  # pragma: no cover\n    return 1\n"}, approved)
        self.assertEqual(self.check(approved), [])

    def test_standing_approval_from_the_base_does_not_count(self) -> None:
        approved = json.loads(json.dumps(BASE_PROFILE))
        approved["guardrails"]["coverage_ratchet"] = {
            "approvals": [{"path": "src/*", "approval": "ADR-12"}]
        }
        base = self.repo.commit({}, approved)
        self.repo.commit({"src/app.py": "def run():  # pragma: no cover\n    return 1\n"})
        findings = coverage_ratchet.run(self.repo.path, approved, ["--base", base])
        self.assertEqual(rules(findings), [("TEST.coverage-ratchet", "src/app.py")])

    def test_placeholder_approval_does_not_count(self) -> None:
        approved = json.loads(json.dumps(BASE_PROFILE))
        approved["guardrails"]["coverage_ratchet"] = {
            "approvals": [{"path": "src/app.py", "approval": "<approval reference>"}]
        }
        self.repo.commit({"src/app.py": "def run():  # pragma: no cover\n    return 1\n"}, approved)
        self.assertEqual(len(self.check(approved)), 1)

    def test_missing_base_is_a_finding(self) -> None:
        findings = coverage_ratchet.run(self.repo.path, BASE_PROFILE, [])
        self.assertEqual(rules(findings), [("TEST.coverage-ratchet", ".")])
        self.assertIn("--base", findings[0].message)


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

    def check(self, profile: dict = BASE_PROFILE) -> list:
        return golden_record.run(self.repo.path, profile, ["--base", self.base])

    def test_changed_golden_without_record_is_reported(self) -> None:
        self.repo.commit({"tests/fixtures/golden/report.txt": "total 4\n"})
        self.assertEqual(
            rules(self.check()),
            [("TEST.golden-change-record", "tests/fixtures/golden/report.txt")],
        )

    def test_record_naming_the_golden_clears_it(self) -> None:
        self.repo.commit(
            {
                "tests/fixtures/golden/report.txt": "total 4\n",
                "docs/golden-changes.md": "# Golden changes\n\n- tests/fixtures/golden/report.txt:"
                " totals now include refunds\n",
            }
        )
        self.assertEqual(self.check(), [])

    def test_record_naming_another_file_does_not_count(self) -> None:
        self.repo.commit(
            {
                "tests/fixtures/golden/report.txt": "total 4\n",
                "docs/golden-changes.md": "# Golden changes\n\n- other.txt: reason\n",
            }
        )
        self.assertEqual(len(self.check()), 1)

    def test_deleted_golden_needs_a_record_and_new_golden_does_not(self) -> None:
        self.repo.commit(
            {"tests/fixtures/golden/report.txt": None, "tests/fixtures/golden/new.txt": "x\n"}
        )
        self.assertEqual(
            rules(self.check()),
            [("TEST.golden-change-record", "tests/fixtures/golden/report.txt")],
        )

    def test_no_golden_globs_means_nothing_to_check(self) -> None:
        self.repo.commit({"tests/fixtures/golden/report.txt": "total 4\n"})
        self.assertEqual(golden_record.run(self.repo.path, {}, ["--base", self.base]), [])

    def test_globs_without_change_record_is_a_profile_finding(self) -> None:
        profile = {"guardrails": {"golden_record": {"globs": ["tests/fixtures/golden/*"]}}}
        findings = golden_record.run(self.repo.path, profile, ["--base", self.base])
        self.assertEqual(rules(findings), [("TEST.golden-change-record", ".omp/test-profile.md")])


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

    def check(self, profile: dict = BASE_PROFILE) -> list:
        return identity_artefacts.run(self.repo.path, profile, ["--base", self.base])

    def test_artefact_change_in_test_only_work_is_reported(self) -> None:
        self.repo.commit(
            {
                "migrations/001_init.sql": "create table t (id bigint);\n",
                "tests/unit/test_app.py": "def test_x():\n    assert True\n",
                "docs/notes.md": "notes\n",
            }
        )
        findings = self.check()
        self.assertEqual(rules(findings), [("TEST.identity-artefacts", "migrations/001_init.sql")])
        self.assertIn("never edit an applied migration", findings[0].message)

    def test_artefact_change_with_production_change_passes(self) -> None:
        self.repo.commit(
            {"migrations/001_init.sql": "create table t (id bigint);\n", "src/app.py": "x = 2\n"}
        )
        self.assertEqual(self.check(), [])

    def test_test_only_work_without_artefacts_passes(self) -> None:
        self.repo.commit({"tests/unit/test_app.py": "def test_x():\n    assert 1\n"})
        self.assertEqual(self.check(), [])

    def test_approved_exemption_disables_the_rule(self) -> None:
        exempt = {**BASE_PROFILE, "identity_artefacts": {"none": "no ids", "approval": "ADR-3"}}
        self.repo.commit({"migrations/001_init.sql": "changed\n"})
        self.assertEqual(self.check(exempt), [])


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

    def check(self, profile: dict = BASE_PROFILE) -> list:
        return timeouts.run(self.repo.path, profile, ["--base", self.base])

    def test_raised_timeout_in_a_changed_test_is_reported(self) -> None:
        self.repo.commit(
            {
                "tests/unit/test_sync.py": (
                    "import subprocess\n\n\ndef test_sync():\n"
                    "    subprocess.run(['sync'], timeout=30, check=True)\n"
                )
            }
        )
        findings = self.check()
        self.assertEqual(rules(findings), [("TEST.no-evasion", "tests/unit/test_sync.py")])
        self.assertIn("raises timeout 'timeout' from 5 to 30", findings[0].message)

    def test_raised_object_style_timeout_is_reported(self) -> None:
        layout = {**BASE_PROFILE["layout"], "test_file_patterns": ["*.test.ts"]}
        profile = {**BASE_PROFILE, "layout": layout}
        self.repo.commit(
            {"tests/unit/sync.test.ts": "it('syncs', async () => {}, { timeout: 9000 });\n"}
        )
        self.assertEqual(
            rules(self.check(profile)), [("TEST.no-evasion", "tests/unit/sync.test.ts")]
        )

    def test_lowered_new_or_production_timeouts_pass(self) -> None:
        self.repo.commit(
            {
                "tests/unit/test_sync.py": (
                    "import subprocess\n\n\ndef test_sync():\n"
                    "    subprocess.run(['sync'], timeout=2, check=True)\n\n\n"
                    "def test_other():\n    subprocess.run(['true'], timeout=60, check=True)\n"
                ),
                "src/app.py": "TIMEOUT_S = 50\nrequest(timeout=50)\n",
            }
        )
        self.assertEqual(self.check(), [])

    def test_adr_backed_exception_waives_the_increase(self) -> None:
        self.repo.commit(
            {
                "tests/unit/test_sync.py": (
                    "import subprocess\n\n\ndef test_sync():\n"
                    "    subprocess.run(['sync'], timeout=30, check=True)\n"
                )
            }
        )
        exception = {"rule": "TEST.no-evasion", "path": "tests/unit/test_sync.py", "adr": "ADR-4"}
        waived = {**BASE_PROFILE, "house_style": {"version": 1, "exceptions": [exception]}}
        self.assertEqual(self.check(waived), [])


AFFECTED_PROFILE = {
    **BASE_PROFILE,
    "adapter": "pytest",
    "tiers": {
        "t1": {"location": "tests/unit"},
        "t2": {"location": "tests/boundary", "selector": "boundary"},
    },
    "coverage": {"scope": ["src/shop"], "line_floor": 90, "branch_floor": 80},
    "verification_surface": {"journey_file_pattern": "journeys/*.journey.py"},
    "ownership_areas": {
        "map": {
            "tests/boundary/test_cart_*.py": "cart",
            "tests/boundary/test_billing_*.py": "billing",
            "journeys/checkout.journey.py": "cart",
            "journeys/invoice.journey.py": "billing",
        }
    },
    "structure": {
        "owners": {
            "files": {"src/shop/pricing.py": "billing"},
            "directories": {"src/shop/": "cart", "src/shop/billing/": "billing"},
        }
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

    def select(self, profile: dict = AFFECTED_PROFILE) -> affected.Selection:
        return affected.affected(self.repo.path, profile, ["--base", self.base])

    def test_change_selects_the_owning_areas_seams_and_journeys(self) -> None:
        self.repo.commit({"src/shop/billing/invoice.py": "x = 2\n"})
        self.assertEqual(
            self.select().paths,
            ("journeys/invoice.journey.py", "tests/boundary/test_billing_flow.py"),
        )

    def test_exact_file_owner_beats_its_directory(self) -> None:
        self.repo.commit({"src/shop/pricing.py": "x = 2\n"})
        self.assertIn("tests/boundary/test_billing_flow.py", self.select().paths)
        self.assertNotIn("tests/boundary/test_cart_flow.py", self.select().paths)

    def test_changed_t2_file_is_selected_and_docs_select_nothing(self) -> None:
        self.repo.commit(
            {
                "tests/boundary/test_cart_flow.py": "def test_a():\n    pass\n\n",
                "docs/readme.md": "x\n",
            }
        )
        self.assertEqual(self.select().paths, ("tests/boundary/test_cart_flow.py",))

    def test_unowned_production_change_selects_all_t2(self) -> None:
        profile = json.loads(json.dumps(AFFECTED_PROFILE))
        profile["structure"]["owners"]["directories"] = {"src/shop/billing/": "billing"}
        self.repo.commit({"src/shop/cart.py": "x = 2\n"})
        selection = self.select(profile)
        self.assertEqual(len(selection.paths), 4)
        self.assertIn("src/shop/cart.py", selection.reason)

    def test_cli_and_adapter_print_the_selection(self) -> None:
        self.repo.commit({"src/shop/cart.py": "x = 2\n"})
        args = ["--repo", str(self.repo.path), "affected", "--base", self.base]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["affected", *args[:2], "--base", self.base])
            adapter_code = cli.main(["adapter", *args])
        self.assertEqual((code, adapter_code), (0, 0))
        lines = out.getvalue().splitlines()
        self.assertEqual(
            lines,
            [
                "journeys/checkout.journey.py",
                "tests/boundary/test_cart_flow.py",
                "-m boundary -- journeys/checkout.journey.py tests/boundary/test_cart_flow.py",
            ],
        )


class DiffCheckCliTests(unittest.TestCase):
    def test_diff_check_runs_all_three_rules_and_exits_one(self) -> None:
        repo = FixtureRepo(self)
        base = repo.commit({"tests/fixtures/golden/report.txt": "a\n"}, BASE_PROFILE)
        repo.commit(
            {"tests/fixtures/golden/report.txt": "b\n", "src/app.py": "x = 1  # pragma: no cover\n"}
        )
        with unittest.mock.patch("sys.stdout") as stdout:
            code = cli.main(["diff-check", "--repo", str(repo.path), "--base", base])
        printed = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertEqual(code, 1)
        self.assertIn("TEST.golden-change-record", printed)
        self.assertIn("TEST.coverage-ratchet", printed)


if __name__ == "__main__":
    unittest.main()
