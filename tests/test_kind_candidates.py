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

from guardrails.commands.structure_naming import kind_candidates, scan_scope
from guardrails.context import changes

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "agent/extensions/agent-rails/engine"
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/structure/kind-candidates.json").read_text()
)


@pytest.mark.fast
class KindCandidateTests(unittest.TestCase):
    def test_each_tree_proposes_exactly_its_candidate_files(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                found = kind_candidates.kind_candidates(
                    FIXTURE["tree"], set(case["changed"]), case.get("config", {})
                )
                assert [item["path"] for item in found] == case["candidates"]

    def test_a_candidate_carries_its_symbols_folder_and_importers(self) -> None:
        (found,) = kind_candidates.kind_candidates(
            FIXTURE["tree"], {"app/models/invoice.py"}, {}
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
        root = kind_candidates.parsed("m.py", source)
        assert kind_candidates.definitions(source, root) == [
            {"signature": "def one(a, b) -> dict[str, int]", "doc": "First line."},
            {"signature": "def two()", "doc": ""},
        ]
        assert kind_candidates.line_at(["a"], 5) == ""

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
        model = kind_candidates.model_of(tree, {})
        assert model.eligible() == ["app/models/fine.py"]
        assert dict(model.graph) == {
            "app/models/broken.py": [
                ("app/views.py", "from app.models import broken, escape, fine")
            ],
            "app/models/escape.py": [
                ("app/views.py", "from app.models import broken, escape, fine")
            ],
            "app/models/fine.py": [
                ("app/views.py", "from app.models import broken, escape, fine")
            ],
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
        graph = kind_candidates.model_of(tree, {}).graph
        assert dict(graph) == {
            "app/util/helpers.py": [
                ("app/models/deep/x.py", "from ...util import helpers"),
                ("app/views.py", "import app.util.helpers"),
            ],
            "a/helpers.py": [("a/main.py", "import helpers")],
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
        tree = kind_candidates.head_tree(self.repo, self.head, ["app/billing/*"])
        assert tree["app/models/invoice.py"] == FIXTURE["tree"]["app/models/invoice.py"]
        assert not any(path.startswith("app/billing/") for path in tree)
        assert kind_candidates.head_tree(self.repo, self.head, ["*"]) == {}

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
        assert scan_scope.proposed_source("pkg/run.py", {"python"})
        assert not scan_scope.proposed_source("pkg/run.pyi", {"python"})
        assert not scan_scope.proposed_source("web/run.ts", {"python", "typescript"})


if __name__ == "__main__":
    unittest.main()
