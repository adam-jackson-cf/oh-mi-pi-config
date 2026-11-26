from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from guardrails.host.process import run_attached

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import approval
from test_verification_surface import LEVER, LeverSelftestCase, surface_repo_config

from guardrails.commands.surface import feature_map, journeys

pytestmark = pytest.mark.seam


JOURNEY = "tests/journeys/test_checkout_journey.py"


def feature_doc(
    title: str = "Checkout", driving: str = f"Driving it with `{LEVER}`"
) -> str:
    return (
        f"# {title}\n\n"
        "## Sub-features\n\nPaying by card ends with an order number on screen.\n\n"
        "## How to get to it (user POV)\n\nOpen the basket and press Pay.\n\n"
        f"## {driving}\n\nRun [the checkout journey](../../{JOURNEY}).\n\n"
        "## Gotchas\n\nSee [sub-features](#sub-features) and [cart](cart.md#gotchas).\n"
    )


class SurfaceRepoTestCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-surface-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)
        run_attached(["git", "init", "-q", str(self.repo)], check=True, timeout=60)
        self.write("docs/features/checkout.md", feature_doc())
        self.write("docs/features/cart.md", feature_doc("Cart"))
        self.write(
            JOURNEY, "def test_checkout(lever):\n    lever.run('scripts/lever')\n"
        )
        self.write("scripts/lever", "#!/bin/sh\n")

    def write(self, name: str, text: str) -> None:
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


class FeatureMapTests(SurfaceRepoTestCase):
    def check(self, repo_config: dict[str, Any] | None = None) -> list[Any]:
        return feature_map.run(self.repo, repo_config or surface_repo_config(), [])

    def test_complete_map_passes(self) -> None:
        assert feature_map.run(self.repo, surface_repo_config(), []) == []

    def test_wrong_or_missing_h2_is_reported(self) -> None:
        self.write(
            "docs/features/cart.md", feature_doc("Cart", "Driving it with something")
        )
        findings = self.check()
        assert [finding.path for finding in findings] == ["docs/features/cart.md"]
        assert "expected exactly" in findings[0].message

    def test_extra_h2_is_reported(self) -> None:
        self.write(
            "docs/features/cart.md", feature_doc("Cart") + "\n## Notes\n\nMore.\n"
        )
        assert len(self.check()) == 1

    def test_headings_inside_code_fences_do_not_count(self) -> None:
        self.write(
            "docs/features/cart.md",
            feature_doc("Cart").replace(
                "## Gotchas\n",
                "## Gotchas\n\n```md\n## Not a heading\n# Nor this\n```\n",
            ),
        )
        assert self.check() == []

    def test_empty_section_is_reported(self) -> None:
        self.write(
            "docs/features/cart.md",
            feature_doc("Cart").replace("Open the basket and press Pay.", ""),
        )
        findings = self.check()
        assert len(findings) == 1
        assert "is empty" in findings[0].message

    def test_duplicate_feature_and_missing_h1_are_reported(self) -> None:
        self.write("docs/features/cart.md", feature_doc("Checkout"))
        self.write(
            "docs/features/payments.md", feature_doc().replace("# Checkout\n", "")
        )
        messages = " | ".join(finding.message for finding in self.check())
        assert "also documented in docs/features/cart.md" in messages
        assert "has 0 H1 headings" in messages

    def test_broken_links_and_anchors_are_reported(self) -> None:
        self.write(
            "docs/features/cart.md",
            feature_doc("Cart").replace("cart.md#gotchas", "missing.md")
            + "\nSee [checkout](checkout.md#no-such-heading) and [web](https://example.com).\n",
        )
        messages = sorted(finding.message for finding in self.check())
        assert len(messages) == 2
        assert "'checkout.md#no-such-heading' names a missing heading" in messages[0]
        assert "'missing.md' does not resolve" in messages[1]

    def test_index_files_are_link_checked_but_not_feature_documents(self) -> None:
        self.write("docs/features/README.md", "# Features\n\n- [Cart](cart.md)\n")
        repo_config = surface_repo_config()
        repo_config["checkers"] = {"feature_map": {"index_files": ["README.md"]}}
        assert self.check(repo_config) == []
        self.write("docs/features/README.md", "# Features\n\n- [Gone](gone.md)\n")
        assert len(self.check(repo_config)) == 1

    def test_missing_map_is_reported_and_exemption_disables_the_rule(self) -> None:
        findings = self.check(surface_repo_config(feature_map_path="docs/nowhere"))
        assert [finding.rule_id for finding in findings] == ["SURFACE.feature-map"]
        exempt = {
            "tests": {"verification_surface": {"none": True}},
            "approvals": [
                approval("exemption", "verification_surface", "library (ADR-4)")
            ],
        }
        assert self.check(exempt) == []


class JourneyTests(SurfaceRepoTestCase):
    def check(self, repo_config: dict[str, Any] | None = None) -> list[Any]:
        return journeys.run(self.repo, repo_config or surface_repo_config(), [])

    def test_mapped_journey_calling_the_lever_passes(self) -> None:
        assert journeys.run(self.repo, surface_repo_config(), []) == []

    def test_journey_not_calling_the_lever_is_reported(self) -> None:
        self.write(JOURNEY, "def test_checkout(client):\n    client.post('/pay')\n")
        findings = self.check()
        assert [finding.path for finding in findings] == [JOURNEY]
        assert "does not call the lever" in findings[0].message

    def test_configured_lever_reference_replaces_the_defaults(self) -> None:
        self.write(JOURNEY, "def test_checkout(drive):\n    run_lever('checkout')\n")
        repo_config = surface_repo_config()
        repo_config["checkers"] = {"journeys": {"lever_references": ["run_lever("]}}
        assert self.check(repo_config) == []

    def test_unmapped_journey_is_reported(self) -> None:
        second = "tests/journeys/test_refund_journey.py"
        self.write(second, "def test_refund():\n    run('scripts/lever')\n")
        findings = self.check()
        assert [finding.path for finding in findings] == [second]
        assert "not linked" in findings[0].message

    def test_misnamed_test_in_journey_directory_is_reported(self) -> None:
        self.write("tests/journeys/test_refund.py", "def test_refund():\n    pass\n")
        self.write("tests/journeys/conftest_data.json", "{}")
        findings = self.check()
        assert [finding.path for finding in findings] == [
            "tests/journeys/test_refund.py"
        ]
        assert "does not match" in findings[0].message

    def test_exempt_surface_has_no_journey_rules(self) -> None:
        self.write(JOURNEY, "def test_checkout():\n    pass\n")
        exempt = {
            "tests": {"verification_surface": {"none": True}},
            "approvals": [
                approval("exemption", "verification_surface", "library (ADR-4)")
            ],
        }
        assert self.check(exempt) == []


class LeverSelftestTests(LeverSelftestCase):
    def test_well_behaved_lever_passes(self) -> None:
        assert self.selftest("good") == []

    def test_doctor_failure_without_marker_is_reported(self) -> None:
        assert "did not print the doctor marker" in self.messages(
            self.selftest("no-marker")
        )

    def test_doctor_passing_in_a_broken_environment_is_reported(self) -> None:
        findings = self.selftest("blind")
        assert "doctor passed in a broken environment" in self.messages(findings)

    def test_child_left_in_the_process_group_is_reported(self) -> None:
        messages = self.messages(self.selftest("leak", settle_s=0.5))
        assert "healthy run left processes behind" in messages
        assert "broken-environment run left processes behind" in messages

    def test_child_escaping_into_a_new_session_is_reported(self) -> None:
        messages = self.messages(self.selftest("escape", settle_s=0.5))
        assert "left processes behind: pid" in messages

    def test_missing_evidence_directory_is_reported(self) -> None:
        findings = self.selftest("no-evidence")
        assert "did not leave the evidence directory" in self.messages(findings)

    def test_failure_env_replaces_the_unusable_data_directory(self) -> None:
        findings = self.selftest(
            "good", failure_env={"OMP_FAKE_DEPENDENCY": "unreachable"}
        )
        assert "doctor passed in a broken environment" in self.messages(findings)


if __name__ == "__main__":
    unittest.main()
