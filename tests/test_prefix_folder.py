"""Committed repositories for the STRUCT.prefix-folder check."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import config, write_config

from guardrails.commands.structure_naming import prefix_folder
from guardrails.context import changes
from guardrails.foundation.findings import catalogue_rules, remediations
from guardrails.foundation.source.tree import parse

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parent / "fixtures/structure/prefix-folder.json"
    ).read_text()
)


TEST_LAYOUT = {"tests": {"layout": {"test_file_patterns": ["test_*.py", "*_test.py"]}}}


def prefixes(paths: list[str], added: set[str]) -> list[str]:
    return [
        found["prefix"]
        for found in prefix_folder.groups(sorted(paths), added, TEST_LAYOUT, "python")
    ]


@pytest.mark.fast
class PrefixGroupTests(unittest.TestCase):
    def test_an_added_member_groups_siblings_sharing_a_leading_token(self) -> None:
        paths = [
            "src/shop/grading/coverage_offline.py",
            "src/shop/grading/coverage_store.py",
            "src/shop/grading/prefix_grade.py",
            "src/shop/grading/prefix_store.py",
            "src/shop/readers.py",
        ]
        added = {
            "src/shop/grading/coverage_offline.py",
            "src/shop/grading/prefix_grade.py",
        }
        found = prefix_folder.groups(paths, added, TEST_LAYOUT, "python")
        assert [item["prefix"] for item in found] == ["coverage", "prefix"]
        for item, producer in zip(
            found, ["coverage_offline", "prefix_grade"], strict=True
        ):
            source = f"src/shop/grading/{producer}.py"
            store = f"src/shop/grading/{item['prefix']}_store.py"
            assert item["folder"] == "src/shop/grading"
            assert item["members"] == sorted([source, store])
            assert item["added_or_moved"] == [source]

    def test_a_prefix_naming_the_folder_is_skipped_but_a_child_folder_is_not(
        self,
    ) -> None:
        paths = [
            "src/shop/grading/coverage/records.py",
            "src/shop/grading/coverage_offline.py",
            "src/shop/grading/coverage_store.py",
            "src/shop/gradings/grading_producer.py",
            "src/shop/gradings/grading_store.py",
        ]
        added = {
            "src/shop/grading/coverage_offline.py",
            "src/shop/gradings/grading_producer.py",
        }
        assert prefixes(paths, added) == ["coverage"]

    def test_tests_init_modifiers_short_tokens_and_skipped_folders_make_no_group(
        self,
    ) -> None:
        paths = [
            "src/shop/test_coverage.py",
            "src/shop/test_prefix.py",
            "src/shop/coverage_test.py",
            "src/shop/prefix_test.py",
            "src/shop/__init__.py",
            "src/shop/init_store.py",
            "src/shop/base_reader.py",
            "src/shop/base_writer.py",
            "src/shop/io_reader.py",
            "src/shop/io_writer.py",
            "src/shop/tests/audit_writer.py",
            "src/shop/tests/audit_store.py",
            "src/shop/.cache/audit_writer.py",
            "src/shop/.cache/audit_store.py",
            "src/skill/SKILL.md",
            "src/skill/audit_writer.py",
            "src/skill/audit_store.py",
            "src/shop/conftest.py",
            "src/shop/conftest_store.py",
        ]
        assert prefixes(paths, set(paths)) == []

    def test_the_leading_name_token_is_the_prefix(self) -> None:
        paths = ["src/shop/cross_mode.py", "src/shop/cross_mode_metrics.py"]
        (found,) = prefix_folder.groups(
            paths, {"src/shop/cross_mode_metrics.py"}, TEST_LAYOUT, "python"
        )
        assert found["prefix"] == "cross"
        assert found["members"] == paths

    def test_single_member_and_partial_words_do_not_make_a_group(self) -> None:
        paths = [
            "src/shop/prefix_grade.py",
            "src/shop/prefixed_store.py",
            "src/shop/standalone.py",
        ]
        assert prefixes(paths, set(paths)) == []

    def test_an_unrelated_addition_does_not_activate_an_existing_group(self) -> None:
        paths = [
            "src/shop/grading/coverage_offline.py",
            "src/shop/grading/coverage_store.py",
            "src/shop/grading/unrelated.py",
        ]
        assert prefixes(paths, {"src/shop/grading/unrelated.py"}) == []


@pytest.mark.fast
class PrefixFolderScoreTests(unittest.TestCase):
    def test_a_pair_score_weights_each_relationship_and_caps_co_change(self) -> None:
        weights = prefix_folder.contracts()["python"]["weights"]
        features = {
            "import_edge": 1.0,
            "shared_reader": 0.0,
            "co_change": 0.5,
            "prefix_is_domain_word": 1.0,
        }
        assert prefix_folder.pair_score(features, weights) == 0.625
        assert prefix_folder.pair_score({**features, "co_change": 3.0}, weights) == 0.75

    def test_domain_words_come_from_docstrings_and_class_names(self) -> None:
        source = (
            '"""Audit\\nevents."""\n\n\nclass InvoiceDrafts:\n'
            "    def total(self):\n        f'''Ledger {self}'''\n"
            "        class Nested:\n            '''Rebate.'''\n"
        )
        words = prefix_folder.domain_words("m.py", prefix_folder.parsed("m.py", source))
        assert {"audit", "event", "invoice", "draft", "nested", "rebate"} <= words
        assert not {"nevent", "ledger"} & words

    def test_script_domain_words_come_from_comments_and_type_names(self) -> None:
        source = (
            "// Audit trail\n/** Invoice drafts */\nexport class LedgerBook {}\n"
            "interface RebateRule { a: number }\ntype OrderLine = { x: 1 };\n"
            "const value = 'Shipment';\n"
        )
        words = prefix_folder.domain_words("m.ts", prefix_folder.parsed("m.ts", source))
        assert {"audit", "trail", "invoice", "draft", "ledger", "book"} <= words
        assert {"rebate", "rule", "order", "line"} <= words
        assert "shipment" not in words
        script = "// Audit\nclass LedgerBook {}\n"
        assert prefix_folder.domain_words(
            "m.js", prefix_folder.parsed("m.js", script)
        ) == {"audit", "ledger", "book"}

    def test_the_cutoff_and_weights_are_per_language(self) -> None:
        contracts = prefix_folder.contracts()
        assert contracts["python"]["cluster_score_cutoff"] == 0.15
        assert set(contracts["python"]["weights"]) == set(prefix_folder.FEATURES)
        assert contracts["typescript"]["weights"] is None
        assert contracts["typescript"]["cluster_score_cutoff"] is None
        assert "go" not in contracts
        assert prefix_folder.measured() == ["python"]

    def test_the_owner_evidence_lives_in_the_catalogue(self) -> None:
        rule = next(
            item for item in catalogue_rules() if item["id"] == prefix_folder.RULE
        )
        assert rule["contract"]["evidence"]["accepted_on"] == "2026-11-23"
        assert remediations()[prefix_folder.RULE].render().startswith("root cause: ")


@pytest.mark.fast
class ImportGraphTests(unittest.TestCase):
    def test_imports_resolve_to_the_nearest_owned_module(self) -> None:
        sources = {
            "pkg/__init__.py": "",
            "pkg/util.py": "",
            "pkg/sub/__init__.py": "",
            "pkg/sub/util.py": "",
            "pkg/sub/main.py": (
                "import util\nfrom ..... import nothing\n"
                "import pkg.util.missing.deeper\nimport nowhere.at.all\n"
                "from pkg import util as u\nfrom pkg.sub import *\n"
            ),
            "a/x/helpers.py": "",
            "a/y/helpers.py": "",
            "a/x/run.py": "import x.helpers\n",
            "c/helpers.py": "",
            "c/use.py": "import helpers\n",
            "tools/helpers.py": "",
            "b/main.py": "import helpers\n",
        }
        found: dict[str, list[prefix_folder.Import] | None] = {
            path: prefix_folder.imports(parse(path, text))
            for path, text in sources.items()
        }
        found["broken.py"] = None
        graph = prefix_folder.ImportGraph(found)
        assert {
            path: sorted(found) for path, found in graph.edges.items() if found
        } == {
            "a/x/run.py": ["a/x/helpers.py"],
            "b/main.py": ["tools/helpers.py"],
            "c/use.py": ["c/helpers.py"],
            "pkg/sub/main.py": [
                "pkg/__init__.py",
                "pkg/sub/__init__.py",
                "pkg/sub/util.py",
                "pkg/util.py",
            ],
        }
        assert "broken.py" not in graph.edges
        assert prefix_folder.domain_words("broken.py", None) == set()

    def test_script_imports_resolve_relative_and_tsconfig_paths(self) -> None:
        texts = {
            "web/tsconfig.json": (
                '{\n  // paths\n  "compilerOptions": {"baseUrl": ".", '
                '"paths": {"@shop/*": ["src/shop/*"]}}\n}\n'
            ),
            "web/src/shop/order-store.ts": "export const store = 1;\n",
            "web/src/shop/orderView.tsx": (
                "import { store } from './order-store';\n"
                "import type { Line } from '@shop/line';\nimport React from 'react';\n"
            ),
            "web/src/shop/line.ts": "export type Line = { a: 1 };\n",
            "web/src/app.js": "const view = require('./shop/orderView.js');\n",
            "web/src/broken.ts": "export const = ;\n",
        }
        evidence = prefix_folder.evidence_of("typescript", texts, [])
        assert {
            path: sorted(found) for path, found in evidence.graph.edges.items() if found
        } == {
            "web/src/app.js": ["web/src/shop/orderView.tsx"],
            "web/src/shop/orderView.tsx": [
                "web/src/shop/line.ts",
                "web/src/shop/order-store.ts",
            ],
        }
        assert "web/src/broken.ts" not in evidence.graph.edges
        assert "web/tsconfig.json" not in evidence.words


@pytest.mark.fast
class ScriptGroupTests(unittest.TestCase):
    def test_script_stems_split_at_separators_and_case_boundaries(self) -> None:
        assert prefix_folder.script_name_tokens("userProfile-store.service") == [
            "user",
            "profile",
            "store",
            "service",
        ]
        assert prefix_folder.script_name_tokens("HTTPClient") == ["httpclient"]

    def test_script_siblings_group_by_their_leading_token(self) -> None:
        paths = [
            "src/shop/orderStore.ts",
            "src/shop/order-view.tsx",
            "src/shop/order.test.ts",
            "src/shop/order_main.py",
            "src/shop/order_view.py",
            "src/orders/orderLine.ts",
            "src/orders/order-view.ts",
        ]
        (found,) = prefix_folder.groups(paths, set(paths), TEST_LAYOUT, "typescript")
        assert (found["folder"], found["prefix"], found["members"]) == (
            "src/shop",
            "order",
            ["src/shop/order-view.tsx", "src/shop/orderStore.ts"],
        )


@pytest.mark.seam
class PrefixFolderCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-prefix-folder-")
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)

    def git(self, repo: Path, *args: str) -> str:
        return run_text(
            ["git", "-C", str(repo), *args], check=True, timeout=10
        ).stdout.strip()

    def commit(self, repo: Path, files: dict[str, str]) -> str:
        for path, text in files.items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", "fixture", "--allow-empty")
        return self.git(repo, "rev-parse", "HEAD")

    def repository(self, case: dict[str, Any]) -> tuple[Path, str, str]:
        repo = self.scratch / str(len(list(self.scratch.iterdir())))
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Fixture")
        self.git(repo, "config", "user.email", "fixture@example.invalid")
        self.commit(repo, FIXTURE["base"])
        base = self.commit(repo, case.get("base_extra", {}))
        return repo, base, self.commit(repo, case["head"])

    def test_each_repository_reports_exactly_its_prefix_clusters(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                repo, base, head = self.repository(case)
                found = prefix_folder.run(
                    repo, case.get("config", {}), ["--base", base, "--head", head]
                )
                assert sorted((item.rule_id, item.path) for item in found) == sorted(
                    tuple(row) for row in case["findings"]
                )

    def test_the_finding_names_the_prefix_members_and_closest_pair(self) -> None:
        repo, base, head = self.repository(FIXTURE["cases"][0])
        (finding,) = prefix_folder.run(repo, {}, ["--base", base, "--head", head])
        assert finding.path == "src/shop/grading/"
        assert "`coverage`" in finding.message
        assert "src/shop/grading/coverage_offline.py" in finding.message
        assert "src/shop/grading/coverage_store.py" in finding.message
        assert "import_edge 1" in finding.message
        assert "`coverage/` folder" in finding.fix

    def test_a_logged_run_writes_a_reviewable_flag_row_per_cluster(self) -> None:
        import contextlib
        import io
        import os
        import sqlite3

        from guardrails.commands import cli

        repo, base, head = self.repository(FIXTURE["cases"][0])
        write_config(repo, config())
        agent = self.scratch / "agent"
        argv = ["prefix-folder", "--repo", str(repo)]
        argv += ["--log-decisions", "prove", "--base", base, "--head", head]
        with (
            patch.dict(os.environ, {"PI_CODING_AGENT_DIR": str(agent)}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            assert cli.main(argv) == 1
        with contextlib.closing(
            sqlite3.connect(agent / "agent-rails/agent-rails.db")
        ) as db:
            rows = db.execute(
                "SELECT source, judgment, question, outcome, item, trigger FROM decisions"
            ).fetchall()
        assert rows == [
            (
                "check",
                "STRUCT.prefix-folder",
                "prefix-folder",
                "flag",
                "src/shop/grading/",
                "prove",
            )
        ]

    def test_a_moved_file_counts_as_added_and_a_modified_one_does_not(self) -> None:
        repo, _, base = self.repository(FIXTURE["cases"][0])
        (repo / "src/shop/grading/scorer.py").rename(
            repo / "src/shop/grading/coverage_scorer.py"
        )
        head = self.commit(repo, {"src/shop/grading/coverage_store.py": "RECORD = 2\n"})
        assert changes.changed_paths(repo, base, head, "AR") == {
            "src/shop/grading/coverage_scorer.py"
        }

    def test_script_clusters_do_not_run_but_yield_candidate_features(self) -> None:
        head_files = {
            "web/src/records/auditLog.ts": "// audit records\nexport const log = 1;\n",
            "web/src/records/audit-view.ts": "import { log } from './auditLog';\n",
            "web/src/records/other.go": "package audit\n",
        }
        repo, base, head = self.repository({"head": head_files})
        assert prefix_folder.run(repo, {}, ["--base", base, "--head", head]) == []
        assert prefix_folder.cluster_candidates(repo, head, "typescript", {}) == [
            {
                "folder": "web/src/records",
                "prefix": "audit",
                "members": [
                    "web/src/records/audit-view.ts",
                    "web/src/records/auditLog.ts",
                ],
                "pairs": [
                    {
                        "pair": [
                            "web/src/records/audit-view.ts",
                            "web/src/records/auditLog.ts",
                        ],
                        "features": {
                            "import_edge": 1.0,
                            "shared_reader": 0.0,
                            "co_change": 1.0,
                            "prefix_is_domain_word": 1.0,
                        },
                    }
                ],
            }
        ]

    def test_unreadable_commits_are_a_finding(self) -> None:
        repo, base, _ = self.repository(FIXTURE["cases"][0])
        (finding,) = prefix_folder.run(repo, {}, ["--base", base, "--head", "missing"])
        assert (finding.rule_id, finding.path) == (prefix_folder.RULE, ".")


if __name__ == "__main__":
    unittest.main()
