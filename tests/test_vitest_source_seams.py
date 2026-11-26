from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import pytest

from guardrails.commands.adapters.vitest import lint
from guardrails.host.process import run_text

pytestmark = pytest.mark.seam

REPO_CONFIG: dict[str, Any] = {
    "tests": {
        "public_entry_points": {"shop": "shop/core.ts:checkout"},
        "adapters": [{"name": "vitest", "tiers": {"t1": {"location": "tests"}}}],
        "layout": {"test_file_patterns": ["*.test.ts", "*.spec.tsx"]},
    }
}


class VitestInventoryTests(unittest.TestCase):
    def test_inventory_scope_waivers_and_no_target_writes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run_text(["git", "init", "-q", str(repo)], check=True, timeout=30)
            paths = ["shop/core.ts", "tests/case.test.ts"]
            for path, source in zip(
                paths,
                ["export function checkout() {}", "vi.mock('../shop/core');"],
                strict=True,
            ):
                destination = repo / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source)
            before = {path: (repo / path).read_bytes() for path in paths}
            found = lint.lint(repo, REPO_CONFIG, [])
            assert [item.rule_id for item in found] == ["TEST.no-self-mocking"]
            repo_config = {
                **REPO_CONFIG,
                "exceptions": [
                    {
                        "rule": "TEST.no-self-mocking",
                        "path": "tests/*",
                        "reason": "Owner-approved boundary replacement",
                        "approvedBy": "example-owner",
                        "approvedAt": "2026-11-24T09:00:00Z",
                    }
                ],
            }
            assert lint.lint(repo, repo_config, []) == []
            assert before == {path: (repo / path).read_bytes() for path in paths}

    def test_inventory_filters_binary_assets_and_reports_unreadable_owned_source(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run_text(["git", "init", "-q", str(repo)], check=True, timeout=30)
            paths = ["shop/broken.ts", "assets/image.png", "unowned/video.ts"]
            for path in paths:
                destination = repo / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"\xff\xfe\x00")
            found = lint.lint(repo, REPO_CONFIG, [])
            assert len(found) == 1
            assert found[0].path == "shop/broken.ts"
            assert found[0].rule_id == "TEST.no-test-hooks"
            assert "cannot read source" in found[0].message

    def test_workspace_names_resolve_to_owned_source_directories(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run_text(["git", "init", "-q", str(repo)], check=True, timeout=30)
            sources = {
                "packages/shop/package.json": '{"name":"@shop/core","exports":{".":"./src/index.ts"}}',
                "packages/shop/src/index.ts": "export function publicApi(testOnly: boolean) { return testOnly; }",
                "tests/case.test.ts": "vi.mock('../packages/shop/src/index'); vi.mock('@shop/core');",
            }
            for path, source in sources.items():
                destination = repo / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(source)
            repo_config = {
                **REPO_CONFIG,
                "tests": {
                    **REPO_CONFIG.get("tests", {}),
                    "public_entry_points": {"@shop/core": "@shop/core:publicApi"},
                },
            }
            found = lint.lint(repo, repo_config, [])
            assert sum(item.rule_id == "TEST.no-self-mocking" for item in found) == 2
            assert any(
                item.rule_id == "TEST.no-test-hooks"
                and item.path == "packages/shop/src/index.ts"
                for item in found
            )

    def test_a_package_json_that_does_not_parse_is_a_finding_not_a_crash(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run_text(["git", "init", "-q", str(repo)], check=True, timeout=30)
            (repo / "shop").mkdir()
            (repo / "shop/core.ts").write_text("export function checkout() {}")
            (repo / "shop/package.json").write_text('{"name": "shop",')
            found = lint.lint(repo, REPO_CONFIG, [])
            assert [(item.rule_id, item.path) for item in found] == [
                ("TEST.no-test-hooks", "shop/package.json")
            ]
            assert "cannot read package.json" in found[0].message

    def test_public_entry_points_in_another_form_are_a_config_finding(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run_text(["git", "init", "-q", str(repo)], check=True, timeout=30)
            repo_config = {
                "tests": {
                    **REPO_CONFIG["tests"],
                    "public_entry_points": {"shop": ["checkout"]},
                }
            }
            (found,) = lint.lint(repo, repo_config, [])
            assert found.rule_id == "CONFIG.complete"
            assert "'<module>:<symbol>'" in found.message

    def test_invalid_owned_syntax_is_a_finding_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            run_text(["git", "init", "-q", str(repo)], check=True, timeout=30)
            (repo / "shop").mkdir()
            (repo / "shop/core.ts").write_text("export function broken( {")
            found = lint.lint(repo, REPO_CONFIG, [])
            assert len(found) == 1
            assert found[0].rule_id == "TEST.no-test-hooks"
            assert "cannot parse source" in found[0].message


if __name__ == "__main__":
    unittest.main()
