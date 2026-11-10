"""TEST.coverage-ratchet, TEST.golden-change-record and TEST.identity-artefacts over git diffs."""

from __future__ import annotations

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
from omp_guardrails.diffs import coverage_ratchet, golden_record, identity_artefacts  # noqa: E402

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
