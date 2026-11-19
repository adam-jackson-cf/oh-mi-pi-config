from __future__ import annotations

import os
import hashlib
import json
import subprocess
import sys
import shutil
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent/extensions/dynaflows/engine"))

from guardrails import cli
from guardrails.structure import layout


class NamingScopeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="omp-naming-scope-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.environment = dict(os.environ, HOME=str(self.root), XDG_CONFIG_HOME=str(self.root / "config"),
                                XDG_CACHE_HOME=str(self.root / "cache"), XDG_DATA_HOME=str(self.root / "data"),
                                GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "scope@example.invalid")
        self.git("config", "user.name", "Scope outcomes")
        self.profile = {
            "structure": {"owners": {"patterns": {"**": "catalogue"}}},
            "guardrails": {"structure": {"root_directories": ["catalogue"], "root_files": []}},
        }
        self.write("legacyUtils/misc.py")
        self.write("catalogue/reader.py")
        self.commit()
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True,
                                       env=self.environment, timeout=30)

    def write(self, path, content="VALUE = 1\n"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")

    def findings(self, *args):
        return cli.run_modules(cli.module_names("path-check", self.profile), self.repo, self.profile, list(args))

    def test_editing_existing_bad_name_does_not_rejudge_names_or_structure(self):
        self.write("legacyUtils/misc.py", "VALUE = 2\n")
        self.assertEqual(self.findings("--event", "edit", "legacyUtils/misc.py"), [])

    def test_new_file_in_legacy_folder_does_not_rejudge_parent(self):
        self.write("legacyUtils/reader.py")
        self.assertEqual(self.findings("--event", "edit", "legacyUtils/reader.py"), [])

    def test_untracked_and_added_names_are_judged(self):
        self.write("catalogue/misc.py")
        for staged in (False, True):
            if staged:
                self.git("add", "catalogue/misc.py")
            with self.subTest(staged=staged):
                self.assertTrue(any(f.rule_id == "NAME.conventions" and f.path == "catalogue/misc.py"
                                    for f in self.findings("--event", "edit", "catalogue/misc.py")))

    def test_new_directory_segments_and_placement_are_judged(self):
        self.write("legacyUtils/misc/reader.py")
        self.write("scratch/reader.py")
        found = self.findings("--event", "edit", "legacyUtils/misc/reader.py", "scratch/reader.py")
        self.assertTrue(any(f.rule_id == "NAME.conventions" and "misc" in f.message for f in found))
        self.assertTrue(any(f.rule_id == "STRUCT.folder-placement" and f.path == "scratch/" for f in found))
        self.assertFalse(any("legacyUtils" in f.message for f in found))

    def test_staged_scope_excludes_modified_and_unstaged_paths(self):
        self.write("legacyUtils/misc.py", "VALUE = 2\n")
        self.git("add", "legacyUtils/misc.py")
        self.write("catalogue/misc.py")
        self.assertEqual(self.findings("--event", "staged"), [])
        self.git("add", "catalogue/misc.py")
        self.assertTrue(any(f.rule_id == "NAME.conventions" for f in self.findings("--event", "staged")))

    def test_staged_rename_checks_destination_only(self):
        self.git("mv", "catalogue/reader.py", "catalogue/misc.py")
        found = self.findings("--event", "staged")
        self.assertTrue(any(f.rule_id == "NAME.conventions" and f.path == "catalogue/misc.py" for f in found))
        self.assertFalse(any("legacyUtils" in f.path for f in found))

    def test_renamed_directory_is_new_even_with_unchanged_file_name(self):
        self.git("mv", "legacyUtils", "misc")
        found = self.findings("--event", "staged")
        self.assertTrue(any(f.rule_id == "NAME.conventions" for f in found))
        self.assertTrue(any(f.rule_id == "STRUCT.folder-placement" and f.path == "misc/" for f in found))

    def test_pr_scope_uses_committed_head_not_dirty_checkout(self):
        self.write("catalogue/misc.py")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        (self.repo / "catalogue/misc.py").unlink()
        self.write("scratch/misc.py")
        found = self.findings("--base", self.base, "--head", head)
        self.assertTrue(any(f.rule_id == "NAME.conventions" and f.path == "catalogue/misc.py" for f in found))
        self.assertFalse(any("scratch" in f.path or "legacyUtils" in f.path for f in found))

    def test_pr_merge_base_keeps_changes_already_on_target_out_of_scope(self):
        self.git("checkout", "-qb", "feature")
        self.write("catalogue/reader.py", "VALUE = 2\n")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        self.write("catalogue/misc.py")
        self.commit()
        target = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.findings("--base", target, "--head", head), [])

    def test_full_scan_remains_explicit_and_no_paths_default(self):
        for arguments in ([], ["--all"]):
            found = self.findings(*arguments)
            self.assertTrue(any(f.rule_id == "NAME.conventions" and "legacyUtils" in f.path for f in found))
            self.assertTrue(any(f.rule_id == "STRUCT.folder-placement" for f in found))

    def test_structure_range_ignores_existing_folder_debt(self):
        self.write("legacyUtils/reader.py")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(layout.run(self.repo, self.profile, ["--base", self.base, "--head", head]), [])

    def test_commit_check_cannot_select_automatic_full_naming_or_structure(self):
        from guardrails import whole_repo

        for checker in ("naming", "structure", "path-check"):
            with self.subTest(checker=checker):
                selected, findings = whole_repo.configured_steps({"guardrails": {"check": {"run": [checker]}}})
                self.assertEqual(selected, [])
                self.assertTrue(any(f.rule_id == "PROFILE.complete" for f in findings))
        self.assertNotIn("naming", whole_repo.DEFAULT_STEPS)
        self.assertNotIn("structure", whole_repo.DEFAULT_STEPS)

    def test_pack_defaults_allow_language_test_placement_without_tier_settings(self):
        for path, content in (
            ("catalogue/test_reader.py", "def test_reader():\n    assert 1 == 1\n"),
            ("catalogue/reader.test.ts", "export const reader = 1;\n"),
            ("catalogue/reader.spec.js", "export const reader = 1;\n"),
            ("catalogue/reader_test.go", "package catalogue\n"),
            ("catalogue/reader.sh", "printf reader\n"),
            ("catalogue/reader.json", "{}\n"),
        ):
            with self.subTest(path=path):
                self.write(path, content)
                found = self.findings("--event", "edit", path)
                self.assertFalse(any(f.rule_id == "TEST.placement" for f in found), found)

    def test_staged_structure_reads_index_contents_not_worktree_contents(self):
        self.profile["guardrails"]["structure"]["forbidden_imports"] = [
            {"root": "catalogue", "modules": ["requests"]},
        ]
        self.write("catalogue/network.py", "import requests\n")
        self.git("add", "catalogue/network.py")
        self.write("catalogue/network.py")
        found = self.findings("--event", "staged")
        self.assertTrue(any(f.rule_id == "STRUCT.dependency-direction" and f.path == "catalogue/network.py"
                            for f in found))

    def test_new_instance_requires_marker_but_existing_instance_is_not_rejudged(self):
        self.profile["guardrails"]["structure"]["instances"] = [
            {"root": "catalogue", "depth": 1, "marker": "manifest.json"},
        ]
        self.write("catalogue/archive/reader.py")
        self.commit()
        self.write("catalogue/archive/writer.py")
        self.assertEqual(self.findings("--event", "edit", "catalogue/archive/writer.py"), [])
        self.write("catalogue/shipping/reader.py")
        found = self.findings("--event", "edit", "catalogue/shipping/reader.py")
        self.assertTrue(any(f.rule_id == "STRUCT.folder-placement" and f.path == "catalogue/shipping/"
                            for f in found))
        self.write("catalogue/shipping/manifest.json", "{}\n")
        self.assertEqual(self.findings("--event", "edit", "catalogue/shipping/reader.py"), [])

    def test_naming_command_shares_event_and_full_scan_scope(self):
        self.write("legacyUtils/misc.py", "VALUE = 2\n")
        modules = cli.module_names("naming", self.profile)
        self.assertEqual(cli.run_modules(modules, self.repo, self.profile,
                                         ["--event", "edit", "legacyUtils/misc.py"]), [])
        self.write("catalogue/misc.py")
        self.git("add", "catalogue/misc.py")
        found = cli.run_modules(modules, self.repo, self.profile, ["--event", "staged"])
        self.assertTrue(any(f.path == "catalogue/misc.py" and f.rule_id == "NAME.conventions"
                            for f in found))
        self.assertFalse(any("legacyUtils" in f.path for f in found))
        found = cli.run_modules(modules, self.repo, self.profile, ["--all"])
        self.assertTrue(any("legacyUtils" in f.path and f.rule_id == "NAME.conventions"
                            for f in found))

    def test_edit_rename_judges_new_name(self):
        self.git("mv", "catalogue/reader.py", "catalogue/misc.py")
        found = self.findings("--event", "edit", "catalogue/misc.py")
        self.assertTrue(any(f.rule_id == "NAME.conventions" and f.path == "catalogue/misc.py"
                            for f in found))

    def test_pr_rename_judges_new_name(self):
        self.git("mv", "catalogue/reader.py", "catalogue/misc.py")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        found = self.findings("--base", self.base, "--head", head)
        self.assertTrue(any(f.rule_id == "NAME.conventions" and f.path == "catalogue/misc.py"
                            for f in found))

    def test_staged_addition_in_legacy_folder_does_not_rejudge_parent(self):
        self.write("legacyUtils/reader.py")
        self.git("add", "legacyUtils/reader.py")
        self.assertEqual(self.findings("--event", "staged"), [])

    def test_pr_content_only_edit_does_not_rejudge_legacy_file(self):
        self.write("legacyUtils/misc.py", "VALUE = 2\n")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        self.assertEqual(self.findings("--base", self.base, "--head", head), [])

    def test_prove_blocks_new_bad_name_but_not_legacy_names_or_placement(self):
        harness = self.root / "harness"
        engine = harness / "agent/extensions/dynaflows/engine"
        engine.mkdir(parents=True)
        source_engine = ROOT / "agent/extensions/dynaflows/engine"
        shutil.copyfile(source_engine / "workflows.py", engine / "workflows.py")
        (engine / "guardrails").symlink_to(source_engine / "guardrails", target_is_directory=True)
        library = harness / "agent/lib"
        library.mkdir(parents=True)
        shutil.copyfile(ROOT / "agent/lib/repo_record.py", library / "repo_record.py")
        record = harness / "repos/scope/catalogue"
        record.mkdir(parents=True)
        (record / "gate.json").write_text(json.dumps({
            "schemaVersion": 2, "checks": {"commit": ["true"]},
            "ownerOnlyPaths": [], "dynaflows": {"enabled": True},
        }))
        (record / "profile.md").write_text("---\n" + json.dumps(self.profile) + "\n---\n")
        self.git("remote", "add", "origin", "https://github.com/scope/catalogue.git")
        manifest = self.root / "manifest.json"
        frozen = json.dumps({"schemaVersion": 1, "slug": "naming", "base": self.base, "proofs": []}).encode()
        manifest.write_bytes(frozen)
        digest = hashlib.sha256(frozen).hexdigest()
        binary = self.root / "bin"
        binary.mkdir()
        gh = binary / "gh"
        gh.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['pr', 'list']:\n"
            "    print(json.dumps([{'headRefOid': args[args.index('--search') + 1], 'number': 1}]))\n"
            "elif args[:2] == ['pr', 'comment']:\n"
            "    print('https://github.com/scope/catalogue/pull/1#issuecomment-1')\n"
            "else:\n"
            "    raise SystemExit('unexpected GitHub request')\n"
        )
        gh.chmod(0o755)
        environment = dict(self.environment, PATH=str(binary) + os.pathsep + os.environ.get("PATH", ""))
        driver = (
            "import json, sys; sys.path.insert(0, sys.argv[1]); import workflows; "
            "r, p, h, m, d, o, t = sys.argv[2:9]; "
            "print(json.dumps(workflows.prove(r, p, h, (m, d), o, target_ref=t)))"
        )

        def prove():
            head = self.git("rev-parse", "HEAD").strip()
            process = subprocess.run(
                [sys.executable, "-I", "-S", "-c", driver, str(engine), str(self.repo),
                 self.base, head, str(manifest), digest, "rename scope", self.base],
                capture_output=True, text=True, env=environment, timeout=60,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            published = self.repo / ".git/omp-prove" / f"{head}.result.json"
            self.assertEqual(json.loads(published.read_text())["status"], result["status"])
            return result

        self.write("legacyUtils/misc.py", "VALUE = 2\n")
        self.write("legacyUtils/reader.py")
        self.commit()
        result = prove()
        self.assertEqual(result["status"], "pass", result["blocking"])
        self.write("catalogue/misc.py")
        self.commit()
        result = prove()
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("catalogue/misc.py: NAME.conventions" in message for message in result["blocking"]))
        self.assertFalse(any("legacyUtils" in message for message in result["blocking"]))

    def test_structure_judgment_payload_marks_introductions_and_keeps_legacy_as_context(self):
        from guardrails.structure import state

        self.write("legacyUtils/reader.py")
        self.write("legacyUtils/shipping/writer.py")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        units = state.extract(self.repo, self.base, head, self.profile)
        legacy = units["legacyUtils"]
        self.assertEqual(legacy["introduced_paths"], [
            "legacyUtils/reader.py", "legacyUtils/shipping/writer.py",
        ])
        self.assertEqual(legacy["introduced_directories"], ["legacyUtils/shipping"])
        self.assertEqual(legacy["files"], legacy["introduced_paths"])
        self.assertTrue(any("legacyUtils/misc.py" in context["files"] for context in legacy["related_context"]))
        self.assertNotIn("folder_name_coverage", legacy["judgment_questions"])
        self.assertNotIn("technical_kind_grouping", legacy["judgment_questions"])
        self.assertNotIn("sibling_naming_pattern", legacy["judgment_questions"])
        self.assertNotIn("overlapping_responsibilities", legacy["judgment_questions"])
        self.assertIn("folder_name_coverage", units["legacyUtils/shipping"]["judgment_questions"])
        self.assertNotIn("legacyUtils/misc.py", legacy["introduced_paths"])
        self.assertEqual(units["legacyUtils/shipping"]["introduced_paths"], [
            "legacyUtils/shipping/writer.py",
        ])

    def test_structure_dispatch_judges_new_ancestor_directories_but_not_legacy_parents(self):
        from guardrails.structure import state

        self.write("catalogue/shipping/parcel/reader.py")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        units = state.extract(self.repo, self.base, head, self.profile)
        self.assertEqual(set(units), {"catalogue/shipping", "catalogue/shipping/parcel"})
        for unit in units.values():
            self.assertIn("folder_name_coverage", unit["judgment_questions"])
            self.assertNotIn("catalogue", unit["introduced_directories"])

    def test_structure_dispatch_does_not_require_legacy_prefix_members_to_move(self):
        from guardrails.structure import state

        self.write("catalogue/audit_reader.py")
        self.commit()
        base = self.git("rev-parse", "HEAD").strip()
        self.write("catalogue/audit_writer.py")
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        unit = state.extract(self.repo, base, head, self.profile)["catalogue"]
        self.assertEqual(unit["files"], ["catalogue/audit_writer.py"])
        self.assertNotIn("prefix_should_be_folder", unit["judgment_questions"])
        self.assertEqual(unit["cluster"], [])

    def test_named_directory_expands_only_its_introduced_files(self):
        self.write("catalogue/reader.py", "VALUE = 2\n")
        self.write("catalogue/misc.py")
        self.write("legacyUtils/writer.py")
        found = self.findings("--event", "edit", "catalogue")
        self.assertTrue(any(f.path == "catalogue/misc.py" and f.rule_id == "NAME.conventions"
                            for f in found))
        self.assertFalse(any(f.path == "catalogue/reader.py" or "legacyUtils" in f.path for f in found))

    def test_configured_python_path_rules_apply_at_edit_commit_and_pr_events(self):
        self.profile["guardrails"]["python"] = {"maintained_roots": ["catalogue", "legacyUtils"]}
        self.write("legacyUtils/writer.py")
        self.assertEqual(self.findings("--event", "edit", "legacyUtils/writer.py"), [])
        self.write("catalogue/Cart.py")
        found = self.findings("--event", "edit", "catalogue/Cart.py")
        self.assertTrue(any(f.rule_id == "PYQ107" and f.path == "catalogue/Cart.py" for f in found))
        self.git("add", "catalogue/Cart.py")
        found = self.findings("--event", "staged")
        self.assertTrue(any(f.rule_id == "PYQ107" and f.path == "catalogue/Cart.py" for f in found))
        self.commit()
        head = self.git("rev-parse", "HEAD").strip()
        found = self.findings("--base", self.base, "--head", head)
        self.assertTrue(any(f.rule_id == "PYQ107" and f.path == "catalogue/Cart.py" for f in found))
        self.assertFalse(any(f.rule_id == "PYQ105" and "legacyUtils" in f.path for f in found))

    def test_path_check_reuses_python_path_rules_without_content_quality(self):
        from guardrails.python_policy import quality

        self.profile["guardrails"]["python"] = {
            "maintained_roots": ["catalogue"], "class_limits": {"max_lines": 1},
        }
        self.write("catalogue/cart.py", "class Cart:\n    def calculate(self):\n        return 1\n")
        found = self.findings("--event", "edit", "catalogue/cart.py")
        self.assertFalse(any(f.rule_id == "PYQ200" for f in found))
        content = quality.run(self.repo, self.profile, ["catalogue/cart.py"])
        self.assertTrue(any(f.rule_id == "PYQ200" for f in content))

    def test_non_python_skill_names_follow_the_same_new_segment_scope(self):
        self.profile["guardrails"]["python"] = {"maintained_roots": ["catalogue"], "skill_roots": ["skills"]}
        self.write("skills/Legacy_Name/guide.md", "Guide\n")
        self.commit()
        self.write("skills/Legacy_Name/usage.md", "Usage\n")
        self.assertEqual(self.findings("--event", "edit", "skills/Legacy_Name/usage.md"), [])
        self.write("skills/New_Skill/guide.md", "Guide\n")
        found = self.findings("--event", "edit", "skills/New_Skill/guide.md")
        self.assertTrue(any(f.rule_id == "PYQ106" for f in found))
