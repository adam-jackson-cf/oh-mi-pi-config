import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import pytest

from guardrails.host.process import run_bytes

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/lib")
)
from test_repo_record import GATE, gate

import repo_record

pytestmark = pytest.mark.seam


PROCESS_TIMEOUT = 60


ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> None:
    run_bytes(["git", *args], check=True, timeout=PROCESS_TIMEOUT)


def record_mode(repo: Path, config: Path) -> str:
    record = repo_record.find_record(repo, config)
    if record is None:
        return "absent"
    repo_record.load_gate(record)
    return "loaded"


class Resolve(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.config = self.temp / "config"
        self.repo = self.temp / "clone"
        git("init", "-q", "-b", "main", str(self.repo))
        git(
            "-C",
            str(self.repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        )

    def write_record(self, slug: str, gate: dict[str, Any]) -> Path:
        record = self.config / "repos" / slug
        record.mkdir(parents=True)
        (record / "gate.json").write_text(json.dumps(gate))
        return record

    def test_resolve_record_names_the_slug_and_the_reason_in_the_twin_s_words(
        self,
    ) -> None:
        assert repo_record.resolve_record(self.repo, self.config) == (
            None,
            None,
            "no origin remote",
        )
        git(
            "-C",
            str(self.repo),
            "remote",
            "add",
            "origin",
            "https://gitlab.com/owner/name.git",
        )
        assert repo_record.resolve_record(self.repo, self.config) == (
            None,
            None,
            "origin https://gitlab.com/owner/name.git is not a GitHub owner/name URL",
        )
        git(
            "-C",
            str(self.repo),
            "remote",
            "set-url",
            "origin",
            "git@github.com:Owner/Name.git",
        )
        gate = self.config / "repos/owner/name/gate.json"
        assert repo_record.resolve_record(self.repo, self.config) == (
            "owner/name",
            None,
            f"no per-repo record at {gate}",
        )
        record = self.write_record("owner/name", {})
        assert repo_record.resolve_record(self.repo, self.config) == (
            "owner/name",
            record,
            None,
        )
        assert (
            repo_record.minimal_text(None, "no origin remote")
            == "dynaflows minimal mode for none: no origin remote"
        )
        assert (
            repo_record.minimal_text("owner/name", "why")
            == "dynaflows minimal mode for owner/name: why"
        )

    def test_no_origin_means_no_record_and_minimal_mode(self) -> None:
        assert repo_record.repo_slug(self.repo) is None
        assert repo_record.find_record(self.repo, self.config) is None
        assert record_mode(self.repo, self.config) == "absent"

    def test_origin_without_a_record_means_minimal_mode(self) -> None:
        git(
            "-C",
            str(self.repo),
            "remote",
            "add",
            "origin",
            "git@github.com:Owner/Name.git",
        )
        assert repo_record.repo_slug(self.repo) == "owner/name"
        assert repo_record.find_record(self.repo, self.config) is None
        assert record_mode(self.repo, self.config) == "absent"

    def test_worktree_resolves_to_the_main_checkout_record(self) -> None:
        git(
            "-C",
            str(self.repo),
            "remote",
            "add",
            "origin",
            "https://github.com/Owner/Name.git",
        )
        record = self.write_record("owner/name", GATE)
        worktree = self.temp / "worktree"
        git(
            "-C",
            str(self.repo),
            "worktree",
            "add",
            "-q",
            "-b",
            "agent/x",
            str(worktree),
        )
        for clone in (self.repo, worktree):
            with self.subTest(clone=clone.name):
                assert repo_record.find_record(clone, self.config) == record
                assert record_mode(clone, self.config) == "loaded"

    def test_complete_record_loads_and_malformed_record_is_an_error(self) -> None:
        git(
            "-C",
            str(self.repo),
            "remote",
            "add",
            "origin",
            "https://github.com/owner/name",
        )
        record = self.write_record("owner/name", gate())
        assert record_mode(self.repo, self.config) == "loaded"
        (record / "gate.json").write_text(json.dumps({"schemaVersion": 2}))
        with pytest.raises(repo_record.RecordError):
            record_mode(self.repo, self.config)
        (record / "gate.json").write_text("{")
        with pytest.raises(repo_record.RecordError):
            record_mode(self.repo, self.config)

    def test_a_record_missing_a_required_field_or_carrying_an_unknown_one_does_not_load(
        self,
    ) -> None:
        git(
            "-C",
            str(self.repo),
            "remote",
            "add",
            "origin",
            "https://github.com/owner/name",
        )
        record = self.write_record("owner/name", GATE)
        assert repo_record.load_gate(record) == GATE
        for invalid, named in (
            (gate(checks={"ci": ["ci"]}), "checks.commit"),
            (gate(checks=None), "checks"),
            (gate(unknownField=3), "unknownField"),
            (gate(extraField=1000), "extraField"),
            (gate(otherField="github"), "otherField"),
            (gate(checks={"commit": ["make"], "lint": ["make"]}), "checks.lint"),
            (gate(ownerOnlyPaths=[]), "ownerOnlyPaths"),
        ):
            with self.subTest(named=named):
                (record / "gate.json").write_text(json.dumps(invalid))
                with pytest.raises(repo_record.RecordError, match=named):
                    repo_record.load_gate(record)
                with pytest.raises(repo_record.RecordError, match=named):
                    record_mode(self.repo, self.config)

    def test_default_root_is_the_checkout_holding_the_module(self) -> None:
        assert repo_record.config_root() == ROOT
        assert repo_record.record_dir("Owner/Name") == ROOT / "repos/owner/name"


if __name__ == "__main__":
    unittest.main()
