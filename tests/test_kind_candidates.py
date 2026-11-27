"""Committed trees for the structure-naming kind-folder file proposer and its kind-candidates command."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import config, write_config

from guardrails.commands.structure_naming import (
    importers,
    kind_candidates,
    outline,
    scan_scope,
)
from guardrails.context import changes
from guardrails.foundation.source.tree import SourceError, parse

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "agent/extensions/agent-rails/engine"
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/structure/kind-candidates.json").read_text()
)
PYTHON = ("python",)


@pytest.mark.fast
class KindCandidateTests(unittest.TestCase):
    def test_each_tree_proposes_exactly_its_candidate_files(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                found = kind_candidates.kind_candidates(
                    FIXTURE["tree"],
                    set(case["changed"]),
                    case.get("config", {}),
                    PYTHON,
                )
                assert [item["path"] for item in found] == case["candidates"]

    def test_a_candidate_carries_its_symbols_folder_and_importers(self) -> None:
        (found,) = kind_candidates.kind_candidates(
            FIXTURE["tree"], {"app/models/invoice.py"}, {}, PYTHON
        )
        assert found["state"] == {
            "file": {
                "path": "app/models/invoice.py",
                "docstring": "Billing invoices.",
                "symbols": ["class Invoice  # A billing invoice."],
            },
            "folder": "app/models",
            "importers": [
                {
                    "path": "app/billing/charge.py",
                    "import": "from app.models.invoice import Invoice",
                }
            ],
        }


@pytest.mark.fast
class KindTreeTests(unittest.TestCase):
    def test_kind_words_match_their_plural_and_singular_forms(self) -> None:
        assert kind_candidates.forms("category") == {
            "category",
            "categories",
            "categorys",
        }
        assert kind_candidates.forms("boxes") == {"boxes", "box"}
        assert kind_candidates.forms("box") == {"box", "boxs", "boxes"}
        assert kind_candidates.forms("day") == {"day", "days"}
        assert kind_candidates.forms("class") == {"class"}

    def test_hidden_vendored_and_skill_folders_are_not_owned(self) -> None:
        assert kind_candidates.excluded("node_modules/x.py", [])
        assert kind_candidates.excluded(".git/x.py", [])
        assert kind_candidates.excluded("a/x.py", ["a"])
        assert kind_candidates.excluded("x.py", [""])
        assert not kind_candidates.excluded("a/x.py", ["b"])

    def test_definitions_read_signatures_docs_and_unparseable_lines(self) -> None:
        source = (
            "def one(a,\n        b) -> dict[str, int]:\n    '''\n\n    First line.\n"
            "    '''\n    return {}\ndef two(): return 1\n"
        )
        found = kind_candidates.parsed("m.py", source)
        assert found is not None
        assert found[1].entries == (
            outline.Entry("def one(a, b) -> dict[str, int]", "First line."),
            outline.Entry("def two()", ""),
        )
        assert importers.line_text("a", 5) == ""

    def test_an_unparseable_file_is_skipped(self) -> None:
        tree = {
            "app/models/broken.py": "def f(): pass\nclass Broken(\n",
            "app/models/escape.py": '"""\\x"""\ndef f(): pass\n',
            "app/models/fine.py": "def g(): pass\n",
            "app/views.py": "from app.models import broken, escape, fine\n",
            "app/models/user.py": "import app.models.fine\nclass Broken(\n",
        }
        assert kind_candidates.parsed("x.py", tree["app/models/broken.py"]) is None
        assert kind_candidates.parsed("x.py", tree["app/models/escape.py"]) is None
        model = kind_candidates.model_of(tree, {}, PYTHON)
        assert model.eligible() == ["app/models/fine.py"]
        line = importers.Importer(
            "app/views.py", "from app.models import broken, escape, fine"
        )
        assert dict(model.graph) == {
            "app/models/broken.py": [line],
            "app/models/escape.py": [line],
            "app/models/fine.py": [line],
        }

    def test_imports_resolve_relative_levels_and_the_nearest_same_named_module(
        self,
    ) -> None:
        tree = {
            "app/__init__.py": "",
            "app/util/__init__.py": "",
            "app/util/helpers.py": "",
            "app/models/__init__.py": "",
            "app/models/deep/__init__.py": "",
            "app/models/deep/x.py": "from ...util import helpers\nfrom .... import nothing\n",
            "app/views.py": "import app.util.helpers\nimport app.util.helpers\n",
            "a/helpers.py": "",
            "b/helpers.py": "",
            "a/main.py": "import helpers\n",
        }
        graph = kind_candidates.model_of(tree, {}, PYTHON).graph
        assert dict(graph) == {
            "app/util/helpers.py": [
                importers.Importer(
                    "app/models/deep/x.py", "from ...util import helpers"
                ),
                importers.Importer("app/views.py", "import app.util.helpers"),
            ],
            "a/helpers.py": [importers.Importer("a/main.py", "import helpers")],
        }


@pytest.mark.seam
class KindCandidatesCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-kind-candidates-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        write_config(self.repo, config(**getattr(self, "repo_config", {})))
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        tree = FIXTURE["tree"]
        self.base = self.commit({
            path: text for path, text in tree.items() if path != "app/models/invoice.py"
        })
        self.head = self.commit({
            "app/models/invoice.py": tree["app/models/invoice.py"]
        })

    def git(self, *args: str) -> str:
        return run_text(
            ["git", "-C", str(self.repo), *args], check=True, timeout=10
        ).stdout.strip()

    def commit(self, files: dict[str, str]) -> str:
        for path, text in files.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_the_command_prints_the_changed_candidate_files(self) -> None:
        result = run_text(
            [
                sys.executable,
                "-I",
                "-c",
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "from guardrails.commands.cli import main; raise SystemExit(main())",
                str(ENGINE),
                "kind-candidates",
                "--repo",
                str(self.repo),
                "--base",
                self.base,
                "--head",
                self.head,
            ],
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        found: list[dict[str, Any]] = json.loads(result.stdout)["candidates"]
        assert [item["path"] for item in found] == ["app/models/invoice.py"]

    def test_the_run_prints_candidates_from_the_head_tree(self) -> None:
        printed = io.StringIO()
        with redirect_stdout(printed):
            assert (
                kind_candidates.run(
                    self.repo, {}, ["--base", self.base, "--head", self.head]
                )
                == []
            )
        found = json.loads(printed.getvalue())["candidates"]
        assert [item["path"] for item in found] == ["app/models/invoice.py"]

    def test_the_head_tree_drops_excluded_paths(self) -> None:
        tree = kind_candidates.head_tree(
            self.repo, self.head, ["app/billing/*"], PYTHON
        )
        assert tree["app/models/invoice.py"] == FIXTURE["tree"]["app/models/invoice.py"]
        assert not any(path.startswith("app/billing/") for path in tree)
        assert kind_candidates.head_tree(self.repo, self.head, ["*"], PYTHON) == {}

    def test_an_unreadable_blob_batch_is_a_finding(self) -> None:
        failed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"bad object")
        with patch.object(changes, "run_bytes", return_value=failed):
            (finding,) = kind_candidates.run(
                self.repo, {}, ["--base", self.base, "--head", self.head]
            )
        assert finding.message == "bad object"

    def test_unreadable_commits_are_a_finding(self) -> None:
        (finding,) = kind_candidates.run(
            self.repo, {}, ["--base", self.base, "--head", "missing"]
        )
        assert (finding.rule_id, finding.path) == ("STRUCT.kind-candidates", ".")


@pytest.mark.fast
class ProposedLanguageTests(unittest.TestCase):
    def test_proposers_read_the_data_languages_and_refuse_ones_they_cannot(
        self,
    ) -> None:
        assert scan_scope.proposed({"python", "go"}) == ("python",)
        with pytest.raises(ValueError, match="python"):
            scan_scope.proposed({"typescript"})
        assert scan_scope.runs("pkg/run.py", {"python"})
        assert not scan_scope.runs("pkg/run.pyi", {"python"})
        assert not scan_scope.runs("web/run.ts", {"python"})


SCRIPT_TREE = {
    "web/tsconfig.json": '{"compilerOptions": {"baseUrl": ".", "paths": {"@app/*": ["src/*"]}}}',
    "web/src/models/invoice.ts": (
        "// Billing invoices.\n"
        "import { Money } from './money';\n\n"
        "/** A billing invoice. */\n"
        "export interface Invoice {\n  total: Money;\n}\n\n"
        "/**\n * Totals one invoice.\n */\n"
        "export const totalOf = (invoice: Invoice): number => invoice.total.cents;\n"
    ),
    "web/src/models/money.ts": "export type Money = { cents: number };\n",
    "web/src/models/index.ts": "export * from './invoice';\n",
    "web/src/models/limits.ts": "export const LIMIT = 10;\n",
    "web/src/billing/charge.ts": (
        "import { totalOf } from '@app/models/invoice';\n"
        "export function charge() { return totalOf; }\n"
    ),
    "web/src/billing/charge.test.ts": "import { totalOf } from '../models/invoice';\n",
}
GO_TREE = {
    "go.mod": "module example.com/shop\n\ngo 1.22\n",
    "internal/models/invoice.go": (
        "//go:build linux\n\n"
        "// Package models holds billing records.\n"
        "package models\n\n"
        "// Invoice is a billing invoice.\n"
        "type Invoice struct {\n\tTotal int\n}\n\n"
        "// Due reports whether the invoice is unpaid.\n"
        "func (i Invoice) Due() bool {\n\treturn i.Total > 0\n}\n"
    ),
    "internal/models/limits.go": "package models\n\nconst Limit = 10\n",
    "internal/billing/charge.go": (
        'package billing\n\nimport (\n\t"fmt"\n\tm "example.com/shop/internal/models"\n)\n\n'
        "func Charge(i m.Invoice) { fmt.Println(i) }\n"
    ),
    "stray/loose.go": 'package stray\n\nimport "example.com/shop/internal/models"\n',
}


@pytest.mark.fast
class KindLanguageTests(unittest.TestCase):
    def test_the_python_languages_propose_no_typescript_or_go_file(self) -> None:
        tree = {**SCRIPT_TREE, **GO_TREE}
        assert kind_candidates.kind_candidates(tree, set(tree), {}, PYTHON) == []

    def test_typescript_kind_files_with_definitions_except_the_folder_index(
        self,
    ) -> None:
        found = kind_candidates.kind_candidates(
            SCRIPT_TREE, set(SCRIPT_TREE), {}, ("typescript",)
        )
        assert [item["path"] for item in found] == [
            "web/src/models/invoice.ts",
            "web/src/models/money.ts",
        ]
        assert found[0]["state"] == {
            "file": {
                "path": "web/src/models/invoice.ts",
                "docstring": "Billing invoices.",
                "symbols": [
                    "export interface Invoice  # A billing invoice.",
                    "export const totalOf = (invoice: Invoice): number =>"
                    "  # Totals one invoice.",
                ],
            },
            "folder": "web/src/models",
            "importers": [
                {
                    "path": "web/src/billing/charge.ts",
                    "import": "import { totalOf } from '@app/models/invoice';",
                },
                {
                    "path": "web/src/models/index.ts",
                    "import": "export * from './invoice';",
                },
                {
                    "path": "web/src/billing/charge.test.ts",
                    "import": "import { totalOf } from '../models/invoice';",
                },
            ],
        }

    def test_go_kind_files_read_package_docs_and_package_importers(self) -> None:
        (found,) = kind_candidates.kind_candidates(GO_TREE, set(GO_TREE), {}, ("go",))
        assert found["state"] == {
            "file": {
                "path": "internal/models/invoice.go",
                "docstring": "Package models holds billing records.",
                "symbols": [
                    "type Invoice struct  # Invoice is a billing invoice.",
                    "func (i Invoice) Due() bool"
                    "  # Due reports whether the invoice is unpaid.",
                ],
            },
            "folder": "internal/models",
            "importers": [
                {
                    "path": "internal/billing/charge.go",
                    "import": 'import m "example.com/shop/internal/models"',
                },
                {
                    "path": "stray/loose.go",
                    "import": 'import "example.com/shop/internal/models"',
                },
            ],
        }

    def test_a_proposer_refuses_a_language_it_cannot_read(self) -> None:
        with pytest.raises(ValueError, match="cannot read rust"):
            kind_candidates.kind_candidates({}, set(), {}, ("rust",))
        assert scan_scope.checked(("go", "python"), {"python", "go"}) == (
            "go",
            "python",
        )


@pytest.mark.fast
class OutlineAndImporterTests(unittest.TestCase):
    def test_folder_entries_are_the_package_marker_and_index_files(self) -> None:
        assert outline.folder_entry("app/models/__init__.py")
        assert outline.folder_entry("web/models/index.tsx")
        assert not outline.folder_entry("web/models/indexer.ts")
        assert not outline.folder_entry("internal/models/doc.go")

    def test_go_type_groups_and_bodiless_functions_have_headers(self) -> None:
        source = (
            "package x\n\n// A is one.\n// More words.\ntype (\n\tA struct{ X int }\n"
            "\tB = int\n)\n\nfunc asm(x int) int\n"
        )
        found = kind_candidates.parsed("x.go", source)
        assert found is not None
        assert found[1].entries == (
            outline.Entry("type A struct", "A is one."),
            outline.Entry("type B = int", "A is one."),
            outline.Entry("func asm(x int) int", ""),
        )
        assert found[1].doc == ""

    def test_script_entries_skip_plain_values_and_line_comments(self) -> None:
        source = (
            "#!/usr/bin/env node\n/* Tools. */\n// Second.\nlet a = 1;\n"
            "// not a doc comment\nenum E { A }\nexport default 3;\n"
            "function* gen() {}\nconst f = function () {}, g = 2;\n"
        )
        found = kind_candidates.parsed("x.ts", source)
        assert found is not None
        assert found[1].doc == "Tools.\nSecond."
        assert found[1].entries == (
            outline.Entry("enum E", ""),
            outline.Entry("function* gen()", ""),
            outline.Entry("const f = function ()", ""),
        )

    def test_go_imports_need_a_module_and_a_readable_go_mod(self) -> None:
        roots = {
            path: parse(path, text)
            for path, text in GO_TREE.items()
            if path.endswith(".go")
        }
        graph = importers.go_importers(GO_TREE, roots)
        assert [item.path for item in graph["internal/models/invoice.go"]] == [
            "internal/billing/charge.go",
            "stray/loose.go",
        ]
        alone = {path: text for path, text in GO_TREE.items() if path != "go.mod"}
        assert importers.go_importers(alone, roots) == {}
        broken = {**GO_TREE, "go.mod": "go 1.22\n"}
        with pytest.raises(SourceError, match="go.mod: go.mod has no module directive"):
            importers.go_importers(broken, roots)

    def test_resolution_reads_tsconfig_for_typescript_and_go_mod_for_go(self) -> None:
        assert importers.manifests(("python", "typescript", "go")) == {
            "tsconfig.json",
            "go.mod",
        }
        assert importers.manifests(PYTHON) == frozenset()


if __name__ == "__main__":
    unittest.main()
