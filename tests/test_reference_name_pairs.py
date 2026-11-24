"""Committed trees for the structure-naming name-pair proposer and its name-pairs command."""

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
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest
from test_agentrails_fixture import (
    config,
    exception as agentrails_exception,
    write_config,
)

from guardrails.commands.structure import name_pairs

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/dynaflows/engine"
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/reference/structure/name-pairs.json").read_text()
)


def proposed(pairs: list[dict[str, Any]]) -> set[tuple[str, frozenset[str]]]:
    return {
        (
            "+".join(pair["features"]["rules"]),
            frozenset((
                pair["state"]["first"]["name"],
                pair["state"]["second"]["name"],
            )),
        )
        for pair in pairs
    }


@pytest.mark.fast
class NamePairTests(unittest.TestCase):
    def test_each_tree_proposes_exactly_its_pairs(self) -> None:
        for case in FIXTURE["cases"]:
            with self.subTest(case=case["name"]):
                pairs = name_pairs.name_pairs(
                    FIXTURE["tree"], set(case["changed"]), case.get("config", {})
                )
                assert proposed(pairs) == {
                    (rule, frozenset((first, second)))
                    for rule, first, second in case["pairs"]
                }

    def test_each_pair_carries_both_names_evidence_and_its_det_score(self) -> None:
        (pair,) = name_pairs.name_pairs(FIXTURE["tree"], {"src/shop/api.py"}, {})
        first, second = pair["state"]["first"], pair["state"]["second"]
        assert (first["kind"], second["kind"]) == ("alias binding", "function")
        assert second["definition"]["signature"] == "def fetch_user(uid):"
        assert second["uses"][0] == (
            "src/shop/api.py:1: from shop.store import fetch_user as get_user"
        )
        assert len(second["uses"]) <= name_pairs.USE_SITES
        assert pair["det_score"] == 0.5 * pair["features"]["name_sim"] + 0.5
        assert pair["id"] == "src/shop/api.py::get_user<>src/shop/store.py::fetch_user"

    def test_external_and_member_names_carry_their_own_evidence(self) -> None:
        tree = {
            "app/__init__.py": "",
            "app/models.py": "class Store:\n    def get(self):\n        return 1\n",
            "app/views.py": (
                "from app.models import Store\nreader = Store.missing\nother = thing.value\n"
                + "print(thing.value, Store.missing)\n" * 4
            ),
        }
        pairs = {
            pair["id"]: pair["state"]["second"]
            for pair in name_pairs.name_pairs(tree, {"app/views.py"}, {})
        }
        external = pairs["app/views.py::other<>::thing.value"]
        member = pairs["app/views.py::reader<>app/models.py::Store.missing"]
        assert (external["kind"], external["definition"]) == (
            "name not defined in this repository",
            None,
        )
        assert external["uses"] == [
            "app/views.py:3: other = thing.value",
            "app/views.py:4: print(thing.value, Store.missing)",
            "app/views.py:5: print(thing.value, Store.missing)",
        ]
        assert member["kind"] == "member of class Store"
        assert member["definition"]["signature"] == "class Store:"
        assert name_pairs.evidence(
            tree, {"name": "x", "kind": "function", "path": "a.py"}
        ) == {
            "name": "x",
            "kind": "function",
            "path": "a.py",
            "definition": None,
            "uses": [],
        }


@pytest.mark.seam
class NamePairsCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-name-pairs-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        write_config(self.repo, config(**getattr(self, "profile", {})))
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        store = {"src/shop/store.py": FIXTURE["tree"]["src/shop/store.py"]}
        self.base = self.commit(store)
        self.head = self.commit({"src/shop/api.py": FIXTURE["tree"]["src/shop/api.py"]})

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

    def pairs(self, *location: str) -> list[dict[str, Any]]:
        result = run_text(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                "from guardrails.commands.cli import main; raise SystemExit(main())",
                str(REFERENCE),
                "name-pairs",
                "--repo",
                str(self.repo),
                *location,
                "--base",
                self.base,
                "--head",
                self.head,
            ],
            check=False,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        found: list[dict[str, Any]] = json.loads(result.stdout)["pairs"]
        return found

    def test_the_command_prints_pairs_touching_the_change(self) -> None:
        assert [pair["id"] for pair in self.pairs()] == [
            "src/shop/api.py::get_user<>src/shop/store.py::fetch_user"
        ]

    def test_a_record_exception_drops_the_pair(self) -> None:
        exception = agentrails_exception(
            "structure-naming/same_thing",
            "src/shop/api.py",
            "get_user is the public name kept for API callers",
        )
        write_config(self.repo, config(exceptions=[exception]))
        assert self.pairs() == []

    def test_the_run_prints_pairs_from_the_head_tree(self) -> None:
        printed = io.StringIO()
        with redirect_stdout(printed):
            assert (
                name_pairs.run(
                    self.repo, {}, ["--base", self.base, "--head", self.head]
                )
                == []
            )
        assert [pair["id"] for pair in json.loads(printed.getvalue())["pairs"]] == [
            "src/shop/api.py::get_user<>src/shop/store.py::fetch_user"
        ]

    def test_a_renamed_file_is_changed_under_its_new_path(self) -> None:
        self.git("mv", "src/shop/api.py", "src/shop/client.py")
        self.git("commit", "-qm", "rename")
        renamed = self.git("rev-parse", "HEAD")
        assert name_pairs.changed_paths(self.repo, self.head, renamed) == {
            "src/shop/client.py"
        }
        assert name_pairs.head_tree(self.repo, renamed, ["*.py"]) == {}

    def test_an_unreadable_blob_batch_is_a_finding(self) -> None:
        failed = SimpleNamespace(returncode=1, stdout=b"", stderr=b"bad object")
        with patch.object(name_pairs, "run_bytes", return_value=failed):
            (finding,) = name_pairs.run(
                self.repo, {}, ["--base", self.base, "--head", self.head]
            )
        assert finding.message == "bad object"

    def test_unreadable_commits_are_a_finding(self) -> None:
        (finding,) = name_pairs.run(
            self.repo, {}, ["--base", self.base, "--head", "missing"]
        )
        assert (finding.rule_id, finding.path) == ("STRUCTURE.name-pairs", ".")


if __name__ == "__main__":
    unittest.main()
