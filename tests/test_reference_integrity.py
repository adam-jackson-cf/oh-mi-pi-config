from __future__ import annotations

import contextlib
import io
import unittest
from collections.abc import Mapping

import pytest
from test_agentrails_fixture import config, write_config
from test_reference_diffs import BASE_PROFILE, FixtureRepo, rules

from guardrails.commands import cli
from guardrails.commands.check import integrity, whole_repo
from guardrails.foundation.findings import Finding

pytestmark = pytest.mark.seam


class IntegrityCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = FixtureRepo(self)
        self.base = self.repo.commit(
            {"src/existing.py": "# existing\nx = 1\n"}, BASE_PROFILE
        )

    def check(self, files: Mapping[str, str | None]) -> list[Finding]:
        self.repo.commit(files)
        return integrity.run(self.repo.path, BASE_PROFILE, ["--base", self.base])


class IntegrityCommentTests(IntegrityCase):
    def test_added_comments_block_in_common_file_syntaxes(self) -> None:
        files = {
            "src/a.py": "x = 1 # explanation\n",
            "src/a.ts": "const x = 1; // explanation\n/* block\n * detail */\n",
            "src/a.sql": "SELECT 1; -- explanation\n",
            "src/block.sql": "SELECT 1;\n/* explanation */\n",
            "src/a.css": "/* explanation */\nbody { color: red; }\n",
            "src/a.graphql": "# explanation\ntype Item { id: ID }\n",
            "docs/a.tex": "% explanation\ntext\n",
            "config/a.properties": "! explanation\nvalue=1\n",
            "config/a.ini": "; explanation\nvalue = 1\n",
            "docs/a.md": "text\n<!-- explanation -->\n",
            "a.toml": "# explanation\nvalue = 1\n",
            "Dockerfile": "# explanation\nFROM scratch\n",
        }
        findings = self.check(files)
        assert {item.path for item in findings} == set(files)
        assert {item.rule_id for item in findings} == {"CODE.no-comments"}

    def test_string_literals_and_rust_attributes_are_not_comments(self) -> None:
        assert (
            self.check({
                "src/a.py": "value = '# not a comment // either'\n",
                "src/a.ts": "const value = 'https://host/#fragment';\nconst template = `// not a comment`;\n",
                "a.toml": 'value = "# not a comment"\n',
                "src/a.sql": "SELECT '-- text', 'it''s -- text';\n",
                "src/a.rs": "#[allow(dead_code)]\nfn main() {}\n",
            })
            == []
        )

    def test_python_module_class_function_docstrings_block_but_data_strings_pass(
        self,
    ) -> None:
        findings = self.check({
            "src/docs.py": '"""module"""\nclass Real:\n    """class"""\n    def run(self):\n        """function"""\n        return "data"\nvalue = "data"\n'
        })
        assert [(item.rule_id, item.message.split(":")[0]) for item in findings] == [
            ("CODE.no-comments", "line 1"),
            ("CODE.no-comments", "line 3"),
            ("CODE.no-comments", "line 5"),
        ]

    def test_unchanged_comments_ignored_and_touched_comment_lines_block(self) -> None:
        assert self.check({"src/existing.py": "# existing\nx = 2\n"}) == []
        findings = self.check({"src/existing.py": "# updated\nx = 2\n"})
        assert [item.rule_id for item in findings] == ["CODE.no-comments"]

    def test_tool_directives_are_exempt(self) -> None:
        python = [
            "#!/usr/bin/env python3",
            "# noqa: E402",
            "# type: ignore",
            "# pyright: strict",
            "# fmt: off",
            "# fmt: on",
            "# ruff: noqa",
            "# isort: skip_file",
            "# pylint: disable=all",
            "# SPDX-License-Identifier: MIT",
            "# coding: utf-8",
            "# -*- mode: python -*-",
            "# vim: set ft=python:",
        ]
        javascript = [
            "// eslint-disable-next-line",
            "// @ts-expect-error",
            "// @ts-ignore",
            "// @ts-nocheck",
            "// prettier-ignore",
            "// biome-ignore lint/all: directive",
            "//go:build linux",
            "//nolint:all",
        ]
        assert (
            self.check({
                "src/directives.py": "\n".join(python) + "\nx = 1\n",
                "src/directives.ts": "\n".join(javascript) + "\nconst x = 1;\n",
                "docs/directives.md": "<!-- markdownlint-disable MD013 -->\n",
            })
            == []
        )

    def test_directive_only_blocks_allow_delimiters_and_decorated_directive_lines(
        self,
    ) -> None:
        assert (
            self.check({
                "src/directives.ts": "/*\n * eslint-disable\n * @ts-ignore\n */\n",
                "docs/directives.md": "<!--\nmarkdownlint-disable MD013\n-->\n",
                "src/single.ts": "/* eslint-disable */\n",
            })
            == []
        )

    def test_directive_blocks_report_only_added_prose_lines(self) -> None:
        findings = self.check({
            "src/directives.ts": "/* eslint-disable\n explanation */\n",
            "docs/directives.md": "<!-- markdownlint-disable\n prose -->\n",
            "src/directives.py": "# noqa: E402\n# explanation\n",
        })
        assert sorted((item.path, item.rule_id, item.message) for item in findings) == [
            (path, "CODE.no-comments", "line 2: added comment")
            for path in ["docs/directives.md", "src/directives.py", "src/directives.ts"]
        ]

    def test_prose_added_inside_existing_directive_blocks_is_reported(self) -> None:
        self.base = self.repo.commit({"src/directives.ts": "/* eslint-disable\n */\n"})
        findings = self.check({
            "src/directives.ts": "/* eslint-disable\n * explanation\n */\n"
        })
        assert [(item.rule_id, item.message) for item in findings] == [
            ("CODE.no-comments", "line 2: added comment")
        ]

    def test_tool_and_parser_directives_of_other_toolchains_are_exempt(self) -> None:
        assert (
            self.check({
                "src/embed.go": "//go:embed assets/*\n//go:generate stringer -type=Kind\npackage main\n",
                "src/env.d.ts": '/// <reference types="vite/client" />\n/// <reference path="./globals.d.ts" />\n',
                "scripts/run.sh": "# shellcheck source=lib.sh\n# shellcheck shell=bash\necho ok\n",
                "config/app.yaml": "# yaml-language-server: $schema=./schema.json\nkey: value\n",
                "Dockerfile": "# syntax=docker/dockerfile:1\n# escape=`\nFROM scratch\n",
            })
            == []
        )

    def test_dockerfile_parser_directives_count_only_on_the_first_lines(self) -> None:
        findings = self.check({
            "Dockerfile": "# syntax=docker/dockerfile:1\nFROM scratch\n# escape=`\n",
            "build.dockerfile": "FROM scratch\n# syntax=docker/dockerfile:1\n",
        })
        assert sorted((item.path, item.message) for item in findings) == [
            ("Dockerfile", "line 3: added comment"),
            ("build.dockerfile", "line 2: added comment"),
        ]

    def test_shell_parameter_expansions_are_not_comments(self) -> None:
        findings = self.check({
            "scripts/count.sh": "if [ $# -eq 0 ]; then\n  echo ${#items[@]} ${path##*/} ${name#prefix}\nfi\necho done # note\n"
        })
        assert [(item.path, item.message) for item in findings] == [
            ("scripts/count.sh", "line 4: added comment")
        ]

    def test_moved_files_report_only_their_added_comment_lines(self) -> None:
        body = "".join(f"value_{index} = {index}\n" for index in range(8))
        self.base = self.repo.commit({"src/old.py": "# existing\n" + body})
        assert (
            self.check({"src/old.py": None, "src/moved.py": "# existing\n" + body})
            == []
        )
        self.base = self.repo.commit({
            "src/moved.py": None,
            "src/old.py": "# existing\n" + body,
        })
        findings = self.check({
            "src/old.py": None,
            "src/again.py": "# existing\n" + body + "extra = 1 # added\n",
        })
        assert [(item.path, item.message) for item in findings] == [
            ("src/again.py", "line 10: added comment")
        ]

    def test_staged_moves_keep_existing_comments_out_of_the_commit_check(self) -> None:
        self.repo.git("mv", "src/existing.py", "src/renamed.py")
        assert integrity.commit_findings(self.repo.path, BASE_PROFILE) == []


class IntegrityCodeTests(IntegrityCase):
    def test_placeholder_bodies_block_and_real_implementations_pass(self) -> None:
        findings = self.check({
            "src/placeholders.py": "def empty():\n    pass\nasync def missing():\n    ...\ndef unsupported():\n    raise NotImplementedError()\ndef real():\n    return 1\n"
        })
        assert [item.rule_id for item in findings] == ["CODE.no-placeholder-bodies"] * 3

    def test_protocol_abstract_overload_and_stub_declarations_are_allowed(self) -> None:
        assert (
            self.check({
                "src/api.py": "from typing import Protocol, overload\nfrom abc import abstractmethod\nclass API(Protocol):\n    def read(self):\n        ...\nclass Base:\n    @abstractmethod\n    def read(self):\n        raise NotImplementedError\n@overload\ndef read(value: int) -> int:\n    ...\n",
                "src/api.pyi": "def read(): ...\n",
            })
            == []
        )

    def test_product_mock_imports_and_calls_block_but_tests_support_and_fixtures_pass(
        self,
    ) -> None:
        sources = {
            "src/a.py": "from unittest import mock\nimport pytest_mock\n",
            "src/a.ts": "import sinon from 'sinon';\nconst mock = require('jest-mock');\nvi.mock('service');\njest.mock('other');\n",
        }
        findings = self.check({
            **sources,
            "tests/unit/test_mock.py": "from unittest.mock import Mock\n",
            "tests/support/mock.ts": "import sinon from 'sinon';\n",
            "tests/fixtures/mock.py": "import mock\n",
        })
        assert {item.path for item in findings} == set(sources)
        assert [item.rule_id for item in findings] == ["CODE.no-product-mocks"] * 6

    def test_excluded_fixture_trees_hold_no_product_code_but_still_ban_comments(
        self,
    ) -> None:
        profile = {
            **BASE_PROFILE,
            "checkers": {
                **BASE_PROFILE["checkers"],
                "check": {"exclude": ["frozen/**"]},
            },
        }
        self.repo.commit({
            "frozen/case.py": "from unittest import mock\nx = 1 # note\n",
            "frozen/case.ts": "vi.mock('service');\n",
        })
        findings = integrity.run(self.repo.path, profile, ["--base", self.base])
        assert [(item.rule_id, item.path) for item in findings] == [
            ("CODE.no-comments", "frozen/case.py")
        ]

    def test_mock_words_in_strings_and_comments_do_not_count_as_imports(self) -> None:
        findings = self.check({
            "src/a.ts": "const text = \"import sinon from 'sinon'; vi.mock('x')\";\n"
        })
        assert findings == []

    def test_commit_check_blocks_uncommitted_comments_even_when_other_checks_excluded(
        self,
    ) -> None:
        profile = {
            **BASE_PROFILE,
            "checkers": {
                **BASE_PROFILE.get("checkers", {}),
                "check": {"run": ["python-quality"], "exclude": ["**"]},
            },
        }
        write_config(self.repo.path, config(**profile))
        (self.repo.path / "src/existing.py").write_text("# existing\nx = 1 # new\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.main([
                "check",
                "--repo",
                str(self.repo.path),
            ])
        assert result == 1
        assert "CODE.no-comments" in output.getvalue()

    def test_staged_comments_cannot_hide_behind_clean_worktree_content(self) -> None:
        target = self.repo.path / "src/existing.py"
        target.write_text("# existing\nx = 1 # staged\n")
        self.repo.git("add", "src/existing.py")
        target.write_text("# existing\nx = 1\n")
        findings = integrity.commit_findings(self.repo.path, BASE_PROFILE)
        assert [item.rule_id for item in findings] == ["CODE.no-comments"]

    def test_new_untracked_files_are_checked_before_staging(self) -> None:
        target = self.repo.path / "src/new.py"
        target.write_text("value = 1 # new\n")
        assert [
            item.rule_id
            for item in integrity.commit_findings(self.repo.path, BASE_PROFILE)
        ] == ["CODE.no-comments"]

    def test_declaration_import_aliases_keep_their_exemptions(self) -> None:
        assert (
            self.check({
                "src/aliases.py": "from typing import Protocol as P, overload as variant\nfrom abc import abstractmethod as abstract\nclass API(P):\n    def read(self): ...\nclass Base:\n    @abstract\n    def read(self): pass\n@variant\ndef read(value: int): ...\n"
            })
            == []
        )

    def test_unchanged_docstrings_remain_while_added_body_comments_block(self) -> None:
        self.base = self.repo.commit({
            "src/docs.py": '"""Existing."""\ndef run():\n    """Existing function."""\n    return 1\n'
        })
        findings = self.check({
            "src/docs.py": '"""Existing."""\ndef run():\n    """Existing function."""\n    return 2 # added\n'
        })
        assert [(item.rule_id, item.message) for item in findings] == [
            ("CODE.no-comments", "line 4: added comment")
        ]

    def test_initial_commit_checks_added_lines_against_empty_tree(self) -> None:
        fresh = FixtureRepo(self)
        target = fresh.path / "initial.py"
        target.write_text("value = 1 # initial\n")
        findings = integrity.commit_findings(fresh.path, BASE_PROFILE)
        assert [item.rule_id for item in findings] == ["CODE.no-comments"]

    def test_explicit_diff_command_blocks_integrity_failures(self) -> None:
        self.repo.commit({"src/app.py": "def run():\n    pass\n"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.main([
                "diff-check",
                "--repo",
                str(self.repo.path),
                "--base",
                self.base,
            ])
        assert result == 1
        assert "CODE.no-placeholder-bodies" in output.getvalue()


class StagedDiffCheckTests(IntegrityCase):
    def stage(self, repo: FixtureRepo, files: Mapping[str, str]) -> None:
        for name, text in files.items():
            target = repo.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        repo.git("add", "-A")

    def staged(self, repo: FixtureRepo) -> list[tuple[str, str]]:
        return rules(whole_repo.staged_diff_findings(repo.path, BASE_PROFILE))

    def test_staged_coverage_golden_growth_and_duplicate_failures_block(self) -> None:
        self.repo.commit({"tests/fixtures/golden/out.txt": "old\n"})
        self.stage(
            self.repo,
            {
                "tests/fixtures/golden/out.txt": "new\n",
                ".coveragerc": "[run]\nomit = src/*\n",
                "tests/unit/test_cart.py": "def test_case():\n    assert total() == 1\n"
                "def test_todo():\n    assert total() == 1\n",
            },
        )
        assert {rule for rule, _ in self.staged(self.repo)} == {
            "TEST.coverage-ratchet",
            "TEST.golden-change-record",
            "TEST.growth.justified",
            "TEST.duplicate-path",
        }

    def test_staged_identity_artefact_in_test_only_work_blocks(self) -> None:
        self.repo.commit({"migrations/001.sql": "CREATE TABLE a (id int);\n"})
        self.stage(
            self.repo,
            {
                "migrations/001.sql": "CREATE TABLE a (id bigint);\n",
                "tests/unit/test_schema.py": "def test_schema_keeps_ids():\n    assert ids()\n",
            },
        )
        assert self.staged(self.repo) == [
            ("TEST.identity-artefacts", "migrations/001.sql")
        ]

    def test_staged_content_is_judged_rather_than_the_worktree(self) -> None:
        self.repo.commit({"tests/fixtures/golden/out.txt": "old\n"})
        target = self.repo.path / "tests/fixtures/golden/out.txt"
        target.write_text("unstaged\n")
        assert self.staged(self.repo) == []
        self.stage(self.repo, {"tests/fixtures/golden/out.txt": "staged\n"})
        target.write_text("old\n")
        assert self.staged(self.repo) == [
            ("TEST.golden-change-record", "tests/fixtures/golden/out.txt")
        ]

    def test_initial_commit_judges_staged_tests_against_empty_tree(self) -> None:
        fresh = FixtureRepo(self)
        self.stage(
            fresh, {"tests/unit/test_first.py": "def test_case():\n    assert run()\n"}
        )
        assert self.staged(fresh) == [
            ("TEST.growth.justified", "tests/unit/test_first.py")
        ]

    def test_commit_check_command_blocks_staged_diff_findings(self) -> None:
        profile = {
            **BASE_PROFILE,
            "checkers": {
                **BASE_PROFILE["checkers"],
                "check": {"run": ["python-quality"], "exclude": ["**"]},
            },
        }
        self.repo.commit({"tests/fixtures/golden/out.txt": "old\n"}, profile)
        self.stage(self.repo, {"tests/fixtures/golden/out.txt": "new\n"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = cli.main([
                "check",
                "--repo",
                str(self.repo.path),
            ])
        assert result == 1
        assert "TEST.golden-change-record" in output.getvalue()


if __name__ == "__main__":
    unittest.main()
