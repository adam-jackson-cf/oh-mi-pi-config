"""Per-repo record resolution against real git clones, worktrees and temp records."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent/lib"))
import repo_record  # noqa: E402

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


def git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


class ParseOrigin(unittest.TestCase):
    def test_each_github_form_maps_to_lowercase_owner_and_name(self):
        for url, slug in ORIGINS.items():
            with self.subTest(url=url):
                self.assertEqual(repo_record.parse_origin(url), slug)

    def test_non_github_or_malformed_origins_have_no_slug(self):
        for url in REFUSED:
            with self.subTest(url=url):
                self.assertIsNone(repo_record.parse_origin(url))


class Resolve(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name)
        self.config = self.temp / "config"
        self.repo = self.temp / "clone"
        git("init", "-q", "-b", "main", str(self.repo))
        git("-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
            "commit", "-q", "--allow-empty", "-m", "init")

    def write_record(self, slug, gate):
        record = self.config / "repos" / slug
        record.mkdir(parents=True)
        (record / "gate.json").write_text(json.dumps(gate))
        return record

    def test_resolve_record_names_the_slug_and_the_reason_in_the_twin_s_words(self):
        self.assertEqual(repo_record.resolve_record(self.repo, self.config), (None, None, "no origin remote"))
        git("-C", str(self.repo), "remote", "add", "origin", "https://gitlab.com/owner/name.git")
        self.assertEqual(repo_record.resolve_record(self.repo, self.config),
                         (None, None, "origin https://gitlab.com/owner/name.git is not a GitHub owner/name URL"))
        git("-C", str(self.repo), "remote", "set-url", "origin", "git@github.com:Owner/Name.git")
        gate = self.config / "repos/owner/name/gate.json"
        self.assertEqual(repo_record.resolve_record(self.repo, self.config),
                         ("owner/name", None, f"no per-repo record at {gate}"))
        record = self.write_record("owner/name", {})
        self.assertEqual(repo_record.resolve_record(self.repo, self.config), ("owner/name", record, None))
        self.assertEqual(repo_record.off_text(None, "no origin remote"), "dynaflows off for none: no origin remote")
        self.assertEqual(repo_record.off_text("owner/name", "why"), "dynaflows off for owner/name: why")

    def test_no_origin_means_no_record_and_dynaflows_off(self):
        self.assertIsNone(repo_record.repo_slug(self.repo))
        self.assertIsNone(repo_record.find_record(self.repo, self.config))
        self.assertFalse(repo_record.dynaflows_enabled(self.repo, self.config))

    def test_origin_without_a_record_means_dynaflows_off(self):
        git("-C", str(self.repo), "remote", "add", "origin", "git@github.com:Owner/Name.git")
        self.assertEqual(repo_record.repo_slug(self.repo), "owner/name")
        self.assertIsNone(repo_record.find_record(self.repo, self.config))
        self.assertFalse(repo_record.dynaflows_enabled(self.repo, self.config))

    def test_worktree_resolves_to_the_main_checkout_record(self):
        git("-C", str(self.repo), "remote", "add", "origin", "https://github.com/Owner/Name.git")
        record = self.write_record("owner/name", {"schemaVersion": 2, "dynaflows": {"enabled": True}})
        worktree = self.temp / "worktree"
        git("-C", str(self.repo), "worktree", "add", "-q", "-b", "agent/x", str(worktree))
        for clone in (self.repo, worktree):
            with self.subTest(clone=clone.name):
                self.assertEqual(repo_record.find_record(clone, self.config), record)
                self.assertTrue(repo_record.dynaflows_enabled(clone, self.config))

    def test_disabled_record_is_off_and_malformed_record_is_an_error(self):
        git("-C", str(self.repo), "remote", "add", "origin", "https://github.com/owner/name")
        record = self.write_record("owner/name", {"schemaVersion": 2, "dynaflows": {"enabled": False}})
        self.assertFalse(repo_record.dynaflows_enabled(self.repo, self.config))
        (record / "gate.json").write_text(json.dumps({"schemaVersion": 2}))
        with self.assertRaises(repo_record.RecordError):
            repo_record.dynaflows_enabled(self.repo, self.config)
        (record / "gate.json").write_text("{")
        with self.assertRaises(repo_record.RecordError):
            repo_record.dynaflows_enabled(self.repo, self.config)

    def test_record_with_a_legacy_dynaflows_contract_field_still_loads(self):
        git("-C", str(self.repo), "remote", "add", "origin", "https://github.com/owner/name")
        gate = {"schemaVersion": 2, "dynaflowsContract": 3, "dynaflows": {"enabled": True}}
        record = self.write_record("owner/name", gate)
        self.assertEqual(repo_record.load_gate(record), gate)
        self.assertTrue(repo_record.dynaflows_enabled(self.repo, self.config))

    def test_default_root_is_the_checkout_holding_the_module(self):
        self.assertEqual(repo_record.config_root(), ROOT)
        self.assertEqual(repo_record.record_dir("Owner/Name"), ROOT / "repos/owner/name")


if __name__ == "__main__":
    unittest.main()
