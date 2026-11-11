"""SURFACE.feature-map, SURFACE.journeys and SURFACE.lever-isolation of the guardrails reference."""

from __future__ import annotations

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/skills/setup-repository-guardrails/assets/python-reference"
FAKE_LEVER = ROOT / "tests/fixtures/reference/surface/fake-lever"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails.surface import feature_map, journeys, lever_selftest  # noqa: E402

LEVER = "scripts/lever"
DOCTOR_MARKER = "LEVER-DOCTOR-FAILED"
JOURNEY = "tests/journeys/test_checkout_journey.py"


def feature_doc(title: str = "Checkout", driving: str = f"Driving it with `{LEVER}`") -> str:
    return (
        f"# {title}\n\n"
        "## Sub-features\n\nPaying by card ends with an order number on screen.\n\n"
        "## How to get to it (user POV)\n\nOpen the basket and press Pay.\n\n"
        f"## {driving}\n\nRun [the checkout journey](../../{JOURNEY}).\n\n"
        "## Gotchas\n\nSee [sub-features](#sub-features) and [cart](cart.md#gotchas).\n"
    )


def surface_profile(**overrides: str) -> dict:
    values = {
        "lever_command": LEVER,
        "doctor_marker": DOCTOR_MARKER,
        "feature_map_path": "docs/features",
        "journey_file_pattern": "tests/journeys/test_*_journey.py",
        "data_dir_arg": "--data-dir",
        "evidence_dir_arg": "--evidence-dir",
    }
    values.update(overrides)
    return {"verification_surface": values, "layout": {"test_file_patterns": ["test_*.py"]}}


class SurfaceRepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-surface-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.write("docs/features/checkout.md", feature_doc())
        self.write("docs/features/cart.md", feature_doc("Cart"))
        self.write(JOURNEY, "def test_checkout(lever):\n    lever.run('scripts/lever')\n")
        self.write("scripts/lever", "#!/bin/sh\n")

    def write(self, name: str, text: str) -> None:
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


class FeatureMapTests(SurfaceRepoTestCase):
    def check(self, profile: dict | None = None) -> list:
        return feature_map.run(self.repo, profile or surface_profile(), [])

    def test_complete_map_passes(self) -> None:
        self.assertEqual(self.check(), [])

    def test_wrong_or_missing_h2_is_reported(self) -> None:
        self.write("docs/features/cart.md", feature_doc("Cart", "Driving it with something"))
        findings = self.check()
        self.assertEqual([finding.path for finding in findings], ["docs/features/cart.md"])
        self.assertIn("expected exactly", findings[0].message)

    def test_extra_h2_is_reported(self) -> None:
        self.write("docs/features/cart.md", feature_doc("Cart") + "\n## Notes\n\nMore.\n")
        self.assertEqual(len(self.check()), 1)

    def test_headings_inside_code_fences_do_not_count(self) -> None:
        self.write(
            "docs/features/cart.md",
            feature_doc("Cart").replace(
                "## Gotchas\n", "## Gotchas\n\n```md\n## Not a heading\n# Nor this\n```\n"
            ),
        )
        self.assertEqual(self.check(), [])

    def test_empty_section_is_reported(self) -> None:
        self.write(
            "docs/features/cart.md",
            feature_doc("Cart").replace("Open the basket and press Pay.", ""),
        )
        findings = self.check()
        self.assertEqual(len(findings), 1)
        self.assertIn("is empty", findings[0].message)

    def test_duplicate_feature_and_missing_h1_are_reported(self) -> None:
        self.write("docs/features/cart.md", feature_doc("Checkout"))
        self.write("docs/features/payments.md", feature_doc().replace("# Checkout\n", ""))
        messages = " | ".join(finding.message for finding in self.check())
        self.assertIn("also documented in docs/features/cart.md", messages)
        self.assertIn("has 0 H1 headings", messages)

    def test_broken_links_and_anchors_are_reported(self) -> None:
        self.write(
            "docs/features/cart.md",
            feature_doc("Cart").replace("cart.md#gotchas", "missing.md")
            + "\nSee [checkout](checkout.md#no-such-heading) and [web](https://example.com).\n",
        )
        messages = sorted(finding.message for finding in self.check())
        self.assertEqual(len(messages), 2)
        self.assertIn("'checkout.md#no-such-heading' names a missing heading", messages[0])
        self.assertIn("'missing.md' does not resolve", messages[1])

    def test_index_files_are_link_checked_but_not_feature_documents(self) -> None:
        self.write("docs/features/README.md", "# Features\n\n- [Cart](cart.md)\n")
        profile = surface_profile()
        profile["guardrails"] = {"feature_map": {"index_files": ["README.md"]}}
        self.assertEqual(self.check(profile), [])
        self.write("docs/features/README.md", "# Features\n\n- [Gone](gone.md)\n")
        self.assertEqual(len(self.check(profile)), 1)

    def test_missing_map_is_reported_and_exemption_disables_the_rule(self) -> None:
        findings = self.check(surface_profile(feature_map_path="docs/nowhere"))
        self.assertEqual([finding.rule_id for finding in findings], ["SURFACE.feature-map"])
        exempt = {"verification_surface": {"none": "library", "approval": "ADR-4"}}
        self.assertEqual(self.check(exempt), [])


class JourneyTests(SurfaceRepoTestCase):
    def check(self, profile: dict | None = None) -> list:
        return journeys.run(self.repo, profile or surface_profile(), [])

    def test_mapped_journey_calling_the_lever_passes(self) -> None:
        self.assertEqual(self.check(), [])

    def test_journey_not_calling_the_lever_is_reported(self) -> None:
        self.write(JOURNEY, "def test_checkout(client):\n    client.post('/pay')\n")
        findings = self.check()
        self.assertEqual([finding.path for finding in findings], [JOURNEY])
        self.assertIn("does not call the lever", findings[0].message)

    def test_configured_lever_reference_replaces_the_defaults(self) -> None:
        self.write(JOURNEY, "def test_checkout(drive):\n    run_lever('checkout')\n")
        profile = surface_profile()
        profile["guardrails"] = {"journeys": {"lever_references": ["run_lever("]}}
        self.assertEqual(self.check(profile), [])

    def test_unmapped_journey_is_reported(self) -> None:
        second = "tests/journeys/test_refund_journey.py"
        self.write(second, "def test_refund():\n    run('scripts/lever')\n")
        findings = self.check()
        self.assertEqual([finding.path for finding in findings], [second])
        self.assertIn("not linked", findings[0].message)

    def test_misnamed_test_in_journey_directory_is_reported(self) -> None:
        self.write("tests/journeys/test_refund.py", "def test_refund():\n    pass\n")
        self.write("tests/journeys/conftest_data.json", "{}")
        findings = self.check()
        self.assertEqual([finding.path for finding in findings], ["tests/journeys/test_refund.py"])
        self.assertIn("does not match", findings[0].message)

    def test_exempt_surface_has_no_journey_rules(self) -> None:
        self.write(JOURNEY, "def test_checkout():\n    pass\n")
        exempt = {"verification_surface": {"none": "library", "approval": "ADR-4"}}
        self.assertEqual(self.check(exempt), [])


class LeverSelftestTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-lever-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)

    def selftest(self, mode: str, **settings: object) -> list:
        lever = shlex.join([sys.executable, str(FAKE_LEVER), "--mode", mode])
        profile = surface_profile(lever_command=lever)
        profile["guardrails"] = {"lever_selftest": {"doctor_args": ["doctor"], **settings}}
        return lever_selftest.run(self.repo, profile, [])

    def messages(self, findings: list) -> str:
        self.assertTrue(all(f.rule_id == "SURFACE.lever-isolation" for f in findings))
        return " | ".join(finding.message for finding in findings)

    def test_well_behaved_lever_passes(self) -> None:
        self.assertEqual(self.selftest("good"), [])

    def test_doctor_failure_without_marker_is_reported(self) -> None:
        self.assertIn("did not print the doctor marker", self.messages(self.selftest("no-marker")))

    def test_doctor_passing_in_a_broken_environment_is_reported(self) -> None:
        findings = self.selftest("blind")
        self.assertIn("doctor passed in a broken environment", self.messages(findings))

    def test_child_left_in_the_process_group_is_reported(self) -> None:
        messages = self.messages(self.selftest("leak", settle_s=0.5))
        self.assertIn("healthy run left processes behind", messages)
        self.assertIn("broken-environment run left processes behind", messages)

    def test_child_escaping_into_a_new_session_is_reported(self) -> None:
        messages = self.messages(self.selftest("escape", settle_s=0.5))
        self.assertIn("left processes behind: pid", messages)

    def test_missing_evidence_directory_is_reported(self) -> None:
        findings = self.selftest("no-evidence")
        self.assertIn("did not leave the evidence directory", self.messages(findings))

    def test_failure_env_replaces_the_unusable_data_directory(self) -> None:
        # The fake lever ignores the environment, so a healthy data directory keeps doctor green
        # and the self-test must report that the broken environment was not detected.
        findings = self.selftest("good", failure_env={"OMP_FAKE_DEPENDENCY": "unreachable"})
        self.assertIn("doctor passed in a broken environment", self.messages(findings))

    def test_incomplete_surface_is_reported(self) -> None:
        profile = surface_profile(doctor_marker="")
        findings = lever_selftest.run(self.repo, profile, [])
        self.assertIn("needs lever_command, doctor_marker", self.messages(findings))


if __name__ == "__main__":
    unittest.main()
