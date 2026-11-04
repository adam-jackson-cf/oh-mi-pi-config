"""Exercise the changed-path gate against real Git allowlists and test layouts."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-paths.py"


class CheckPathsTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="omp-path-check-test-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)
        self.git("init", "-q")
        (self.repo / ".gitignore").write_text("*\n!.gitignore\n")
        self.git("add", ".gitignore")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True)

    def file(self, path, tracked=False, allowed=False):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("# test input\n")
        if allowed:
            with (self.repo / ".gitignore").open("a") as ignore:
                for parent in reversed(file.relative_to(self.repo).parents):
                    if parent != Path("."):
                        ignore.write(f"!{parent.as_posix()}/\n")
                ignore.write(f"!{path}\n")
        if tracked:
            self.git("add", "-f", "--", path)
        return file

    def check(self, *paths, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, paths)],
                              cwd=cwd or self.repo, capture_output=True, text=True)

    def assert_clean(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def assert_error(self, result, path, fix):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        self.assertTrue(lines[0].startswith(f"{path}: "), result.stdout)
        self.assertIn(fix, lines[0])

    def test_tracked_ignored_file_passes(self):
        self.file("source.txt", tracked=True)
        self.assert_clean(self.check("source.txt"))

    def test_new_ignored_file_names_allowlist_fix(self):
        self.file("some/new/ignored.txt")
        self.assert_error(self.check("some/new/ignored.txt"), "some/new/ignored.txt",
                          "add a !some/new/ignored.txt re-include")

    def test_untracked_reincluded_file_passes(self):
        self.file("source.txt", allowed=True)
        self.assert_clean(self.check("source.txt"))

    def test_misplaced_tests_require_root_test_folder(self):
        for path in ("foo.test.ts", "src/test_feature.py", "tests/nested/foo.test.ts"):
            with self.subTest(path=path):
                self.file(path, allowed=True)
                self.assert_error(self.check(path), path, "move test files directly under tests/")

    def test_test_sources_require_test_names(self):
        for path, pattern in (("tests/helper.py", "test_*.py"), ("tests/helper.ts", "*.test.ts")):
            with self.subTest(path=path):
                self.file(path, allowed=True)
                self.assert_error(self.check(path), path, f"rename test files to {pattern}")

    def test_named_tests_and_non_source_fixtures_pass(self):
        paths = ("tests/test_feature.py", "tests/feature.test.ts", "tests/fixture.json")
        for path in paths:
            self.file(path, allowed=True)
        self.assert_clean(self.check(*paths))

    def test_deleted_path_skips_all_rules(self):
        file = self.file("misplaced.test.ts", tracked=True)
        file.unlink()
        self.assert_clean(self.check("misplaced.test.ts"))

    def test_no_arguments_checks_tracked_files_only(self):
        self.file("source.txt", tracked=True)
        self.file("ignored.txt")
        self.assert_clean(self.check())
        self.file("misplaced.test.ts", tracked=True)
        self.assert_error(self.check(), "misplaced.test.ts", "move test files directly under tests/")

    def test_subdirectory_arguments_resolve_against_working_directory(self):
        self.file("source/feature.test.ts", allowed=True)
        self.file("ignored.txt")
        cwd = self.repo / "source"
        self.assert_error(self.check("feature.test.ts", cwd=cwd), "source/feature.test.ts",
                          "move test files directly under tests/")
        self.assert_error(self.check("../ignored.txt", cwd=cwd), "ignored.txt",
                          "add a !ignored.txt re-include")

    def test_no_arguments_from_subdirectory_still_checks_repository(self):
        self.file("source/value.txt", tracked=True)
        self.file("misplaced.test.ts", tracked=True)
        self.assert_error(self.check(cwd=self.repo / "source"), "misplaced.test.ts",
                          "move test files directly under tests/")

    def test_absolute_paths_use_repository_relative_diagnostics(self):
        file = self.file("some ignored.txt")
        self.assert_error(self.check(file), "some ignored.txt", "add a !some ignored.txt re-include")

    def test_absolute_directory_alias_uses_repository_path(self):
        self.file("ignored.txt")
        alias = self.repo / "alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        self.assert_error(self.check(alias / "ignored.txt"), "ignored.txt",
                          "add a !ignored.txt re-include")

    def test_dangling_symlink_is_checked_without_following_target(self):
        (self.repo / "link.txt").symlink_to(self.repo / "missing.txt")
        self.assert_error(self.check("link.txt"), "link.txt", "add a !link.txt re-include")

    def test_batch_preserves_spaces_and_reports_each_ignored_path(self):
        paths = ("first ignored.txt", "second.txt")
        for path in paths:
            self.file(path)
        result = self.check(*paths)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 2)
        for path in paths:
            self.assertIn(f"{path}: ignored by the .gitignore allowlist; add a !{path} re-include",
                          result.stdout)


if __name__ == "__main__":
    unittest.main()
