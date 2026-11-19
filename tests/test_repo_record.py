"""Per-repo record resolution against real git clones, worktrees and temp records."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent/lib"))
import repo_record

PROCESS_TIMEOUT = 60
ROOT = Path(__file__).resolve().parents[1]

ORIGINS = {
    "https://github.com/Owner-1/Repo.Name": "owner-1/repo.name",
    "https://github.com/Owner-1/Repo.Name.git": "owner-1/repo.name",
    "git@github.com:Owner-1/Repo_Name": "owner-1/repo_name",
    "git@github.com:Owner-1/Repo_Name.git": "owner-1/repo_name",
    "ssh://git@github.com/Owner-1/repo-name": "owner-1/repo-name",
    "ssh://git@github.com/Owner-1/repo-name.git\n": "owner-1/repo-name",
}
REFUSED = [
    "https://gitlab.com/owner/name.git",
    "https://github.com/owner",
    "https://github.com/owner/name/extra",
    "https://github.com/../name",
    "git@github.com:owner/..",
    "file:///tmp/owner/name",
    "",
]


# A complete gate.json; tests vary one field at a time.
GATE = {"schemaVersion": 2, "checks": {"commit": ["make", "gate"]},
        "ownerOnlyPaths": [], "dynaflows": {"enabled": True}}


def gate(**fields: object) -> dict[str, Any]:
    """GATE with `fields` replacing its values; a None value removes the field."""
    merged = {**GATE, **fields}
    return {key: value for key, value in merged.items() if value is not None}


def raised(error: type[Exception], action: Callable[[], object]) -> Exception:
    """The ``error`` that ``action`` raises; fail the test when it raises none."""
    try:
        action()
    except error as caught:
        return caught
    raise AssertionError(f"{error.__name__} not raised")


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, timeout=PROCESS_TIMEOUT)


class ParseOrigin(unittest.TestCase):
    def test_each_github_form_maps_to_lowercase_owner_and_name(self) -> None:
        for url, slug in ORIGINS.items():
            with self.subTest(url=url):
                assert repo_record.parse_origin(url) == slug

    def test_non_github_or_malformed_origins_have_no_slug(self) -> None:
        for url in REFUSED:
            with self.subTest(url=url):
                assert repo_record.parse_origin(url) is None


class Resolve(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.config = self.temp / "config"
        self.repo = self.temp / "clone"
        git("init", "-q", "-b", "main", str(self.repo))
        git("-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-q", "--allow-empty", "-m", "init")

    def write_record(self, slug: str, gate: dict[str, Any]) -> Path:
        record = self.config / "repos" / slug
        record.mkdir(parents=True)
        (record / "gate.json").write_text(json.dumps(gate))
        return record

    def test_resolve_record_names_the_slug_and_the_reason_in_the_twin_s_words(self) -> None:
        assert repo_record.resolve_record(self.repo, self.config) == (None, None, "no origin remote")
        git("-C", str(self.repo), "remote", "add", "origin", "https://gitlab.com/owner/name.git")
        assert repo_record.resolve_record(self.repo, self.config) == (None, None, "origin https://gitlab.com/owner/name.git is not a GitHub owner/name URL")
        git("-C", str(self.repo), "remote", "set-url", "origin", "git@github.com:Owner/Name.git")
        gate = self.config / "repos/owner/name/gate.json"
        assert repo_record.resolve_record(self.repo, self.config) == ("owner/name", None, f"no per-repo record at {gate}")
        record = self.write_record("owner/name", {})
        assert repo_record.resolve_record(self.repo, self.config) == ("owner/name", record, None)
        assert repo_record.off_text(None, "no origin remote") == "dynaflows off for none: no origin remote"
        assert repo_record.off_text("owner/name", "why") == "dynaflows off for owner/name: why"

    def test_no_origin_means_no_record_and_dynaflows_off(self) -> None:
        assert repo_record.repo_slug(self.repo) is None
        assert repo_record.find_record(self.repo, self.config) is None
        assert not repo_record.dynaflows_enabled(self.repo, self.config)

    def test_origin_without_a_record_means_dynaflows_off(self) -> None:
        git("-C", str(self.repo), "remote", "add", "origin", "git@github.com:Owner/Name.git")
        assert repo_record.repo_slug(self.repo) == "owner/name"
        assert repo_record.find_record(self.repo, self.config) is None
        assert not repo_record.dynaflows_enabled(self.repo, self.config)

    def test_worktree_resolves_to_the_main_checkout_record(self) -> None:
        git("-C", str(self.repo), "remote", "add", "origin", "https://github.com/Owner/Name.git")
        record = self.write_record("owner/name", GATE)
        worktree = self.temp / "worktree"
        git("-C", str(self.repo), "worktree", "add", "-q", "-b", "agent/x", str(worktree))
        for clone in (self.repo, worktree):
            with self.subTest(clone=clone.name):
                assert repo_record.find_record(clone, self.config) == record
                assert repo_record.dynaflows_enabled(clone, self.config)

    def test_disabled_record_is_off_and_malformed_record_is_an_error(self) -> None:
        git("-C", str(self.repo), "remote", "add", "origin", "https://github.com/owner/name")
        record = self.write_record("owner/name", gate(dynaflows={"enabled": False}))
        assert not repo_record.dynaflows_enabled(self.repo, self.config)
        (record / "gate.json").write_text(json.dumps({"schemaVersion": 2}))
        raised(repo_record.RecordError, partial(repo_record.dynaflows_enabled, self.repo, self.config))
        (record / "gate.json").write_text("{")
        raised(repo_record.RecordError, partial(repo_record.dynaflows_enabled, self.repo, self.config))

    def test_a_record_missing_a_required_field_or_carrying_an_unknown_one_does_not_load(self) -> None:
        git("-C", str(self.repo), "remote", "add", "origin", "https://github.com/owner/name")
        record = self.write_record("owner/name", GATE)
        assert repo_record.load_gate(record) == GATE
        for invalid, named in ((gate(checks={"ci": ["ci"]}), "checks.commit"),
                               (gate(checks=None), "checks"), (gate(unknownField=3), "unknownField"),
                               (gate(extraField=1000), "extraField"), (gate(otherField="github"), "otherField"),
                               (gate(checks={"commit": ["make"], "lint": ["make"]}), "checks.lint"),
                               (gate(ownerOnlyPaths=None), "ownerOnlyPaths")):
            with self.subTest(named=named):
                (record / "gate.json").write_text(json.dumps(invalid))
                assert re.search(named, str(raised(repo_record.RecordError, partial(repo_record.load_gate, record))))
                assert re.search(named, str(raised(repo_record.RecordError,
                                                   partial(repo_record.dynaflows_enabled, self.repo, self.config))))

    def test_default_root_is_the_checkout_holding_the_module(self) -> None:
        assert repo_record.config_root() == ROOT
        assert repo_record.record_dir("Owner/Name") == ROOT / "repos/owner/name"


class GateReason(unittest.TestCase):
    def test_complete_gates_with_optional_fields_are_accepted(self) -> None:
        for valid in (GATE, gate(agentMerge="disabled"),
                      gate(ownerOnlyPaths=["scripts/checks/dependency-audit-exceptions.json", "security/"]),
                      gate(checks={"commit": ["c"], "ci": ["true"], "format": ["ruff", "format"], "profile": ["p"]},
                           ci={"requiredContext": "gate", "runner": "ci-gate"}, dynaflows={"enabled": False})):
            with self.subTest(gate=valid):
                assert repo_record.gate_reason(valid) is None

    def test_each_invalid_gate_names_its_reason(self) -> None:
        cases = [
            ({"schemaVersion": True}, "unsupported gate schemaVersion"),
            (gate(schemaVersion=3), "unsupported gate schemaVersion"),
            (gate(checks=None), "checks must be an object with checks.commit"),
            (gate(checks={"ci": ["true"]}), "checks.commit must be argv"),
            (gate(checks={"commit": []}), "checks.commit must be argv"),
            (gate(checks={"commit": ["c"], "ci": None}), "checks.ci must be argv"),
            (gate(checks={"commit": ["c"], "format": "ruff format"}), "checks.format must be argv"),
            (gate(checks={"commit": ["c"], "profile": []}), "checks.profile must be argv"),
            (gate(checks={"commit": ["c"], "lint": ["x"]}), "unknown field checks.lint must be removed"),
            (gate(unknownField=3), "unknown field unknownField must be removed"),
            (gate(unknownField=3, extraField=5), "unknown field extraField, unknownField must be removed"),
            (gate(otherField="github"), "unknown field otherField must be removed"),
            (gate(agentMerge=False), "agentMerge must be allowed or disabled"),
            (gate(ci="gate"), "ci must declare nonblank requiredContext and runner"),
            (gate(ci={"requiredContext": "gate"}), "ci must declare nonblank requiredContext and runner"),
            (gate(ci={"requiredContext": " ", "runner": "ci-gate"}), "ci must declare nonblank requiredContext and runner"),
            (gate(ci={"requiredContext": "gate", "runner": 1}), "ci must declare nonblank requiredContext and runner"),
            (gate(ownerOnlyPaths=None), "ownerOnlyPaths must be a list of repo paths (empty when the repo has none)"),
            (gate(ownerOnlyPaths="a.json"), "ownerOnlyPaths must be a list of repo paths (empty when the repo has none)"),
            (gate(ownerOnlyPaths=[1]), "ownerOnlyPaths entries must be strings"),
            (gate(ownerOnlyPaths=["a.json", "a.json"]), "ownerOnlyPaths entries must be unique"),
        ]
        cases += [(gate(ownerOnlyPaths=[path]),
                   f"ownerOnlyPaths entry {json.dumps(path)} must be a repo-relative POSIX path (a trailing / names a directory)")
                  for path in ("", "/", "/abs.json", "a//b", "./a", "a/../b", "..", "a\\b", "a\0b")]
        cases += [(gate(dynaflows=switch), 'dynaflows must be {"enabled": true|false}')
                  for switch in (None, {}, {"enabled": 1}, {"enabled": "true"}, True)]
        for invalid, reason in cases:
            with self.subTest(gate=invalid):
                assert repo_record.gate_reason(invalid) == reason


class OwnerOnly(unittest.TestCase):
    def test_a_file_entry_matches_itself_and_a_directory_entry_matches_what_lies_under_it(self) -> None:
        owned = ["scripts/checks/exceptions.json", "security/"]
        for path in ("scripts/checks/exceptions.json", "security/a.json", "security/deep/b.json"):
            with self.subTest(path=path):
                assert repo_record.owner_only(path, owned)
        for path in ("scripts/checks/exceptions.json.bak", "scripts/checks", "securityx/a.json", "security"):
            with self.subTest(path=path):
                assert not repo_record.owner_only(path, owned)
        assert not repo_record.owner_only("anything", [])


if __name__ == "__main__":
    unittest.main()
