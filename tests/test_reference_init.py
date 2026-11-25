"""guardrails init: drafts the setup config sections outside the repo and writes nothing into it."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)

import pytest
from test_agentrails_fixture import approval, config, fixture, write_config

from guardrails.commands import cli
from guardrails.commands.init import (
    lsp,
    native_config,
    native_languages,
    sections_draft,
)
from guardrails.context.config.house_style import load_house_style

pytestmark = pytest.mark.seam

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/agent-rails/engine"

CATALOGUE = REFERENCE / "guardrails/data/guardrail-rules.json"
# Repositories stay unaware of the harness: its names may not appear in what init proposes or prints.
# The git-excluded .agentrails/ directory is the one allowed mention.
HARNESS_NAME = re.compile(r"agent-rails|(?<!\.)\bagentrails\b|harness", re.IGNORECASE)
OFFLINE = ("--offline",)
# A language server speaking just enough LSP over stdio: it asks the client for configuration
# (which the client must answer) before it answers a references query with one location.
FAKE_SERVER = """import json, sys

def read():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if not line.strip():
            break
        name, _, value = line.decode().partition(":")
        if name.lower() == "content-length":
            length = int(value)
    return json.loads(sys.stdin.buffer.read(length))

def send(message):
    body = json.dumps({"jsonrpc": "2.0", **message}).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\\r\\n\\r\\n" % len(body) + body)
    sys.stdout.buffer.flush()

while True:
    message = read()
    method = message.get("method")
    if method == "initialize":
        send({"id": message["id"], "result": {"capabilities": {"referencesProvider": True}}})
    elif method == "textDocument/references":
        send({"id": "ask", "method": "workspace/configuration", "params": {"items": [{}]}})
        answer = read()
        assert answer.get("id") == "ask" and answer.get("result") == [None], answer
        uri = message["params"]["textDocument"]["uri"]
        span = {"start": message["params"]["position"], "end": message["params"]["position"]}
        send({"id": message["id"], "result": [{"uri": uri, "range": span}]})
    elif method == "shutdown":
        send({"id": message["id"], "result": None})
    elif method == "exit":
        sys.exit(0)
"""


def clean_environment() -> dict[str, str]:
    return {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }


def git(repo: Path, *args: str) -> str:
    result = run_text(
        ["git", "-C", str(repo), *args],
        check=False,
        env=clean_environment(),
        timeout=60,
    )
    return result.stdout.strip()


def snapshot(root: Path) -> dict[str, object]:
    """Every file's bytes and mode outside .git, plus the hook path setting."""
    files = {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode)
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts[:1]
    }
    return {"files": files, "hooksPath": git(root, "config", "--get", "core.hooksPath")}


def proposed(output: str, name: str) -> str:
    """The text init printed for one proposed native config."""
    lines = output.splitlines()
    start = lines.index(f"--- begin {name}") + 1
    return "\n".join(lines[start : lines.index(f"--- end {name}", start)]) + "\n"


class InitCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="omp-init-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve() / "repo"
        self.out = Path(self.temporary.name).resolve() / "drafts/sections.json"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname = "target"\nrequires-python = ">=3.11"\n'
        )
        self.server = self.executable("pyright-langserver", FAKE_SERVER)
        self.executable("typescript-language-server", FAKE_SERVER)
        self.use_path(self.server.parent, os.environ.get("PATH", ""))

    def use_path(self, *directories: str | Path) -> None:
        search = os.pathsep.join(str(directory) for directory in directories)
        path = mock.patch.dict(os.environ, {"PATH": search})
        path.start()
        self.addCleanup(path.stop)

    def executable(self, name: str, body: str) -> Path:
        path = self.repo.parent / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)
        return path

    def init(self, *args: str, offline: bool = True) -> tuple[int, str]:
        output = io.StringIO()
        location = ["--repo", str(self.repo), "--out", str(self.out)]
        with contextlib.redirect_stdout(output):
            code = cli.main(["init", *location, *(OFFLINE if offline else ()), *args])
        return code, output.getvalue()

    def write(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def draft(self) -> dict[str, Any]:
        drafted: dict[str, Any] = json.loads(self.out.read_text())
        return drafted

    def assert_second_run_changes_nothing(self, *args: str) -> None:
        before = snapshot(self.out.parent)
        code, output = self.init(*args)
        assert code == 0, output
        assert "no changes" in output
        assert snapshot(self.out.parent) == before


MAKEFILE = """.PHONY: gate-commit gate-ci
gate-commit:
\t@echo commit
gate-ci: lint
\t@echo ci
check-paths:
\t@echo paths
VAR := value
"""
WORKFLOW = """name: gate
on: [push]
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        name: checkout
  docs:
    name: Build docs
    runs-on: ubuntu-latest
"""


class DraftSectionTests(InitCase):
    def test_drafts_the_setup_sections_outside_and_never_touches_the_repo(
        self,
    ) -> None:
        self.write("Makefile", MAKEFILE)
        self.write(".github/workflows/gate.yml", WORKFLOW)
        before = snapshot(self.repo)
        code, output = self.init()
        assert code == 0, output
        assert snapshot(self.repo) == before
        drafted = self.draft()
        assert tuple(drafted) == sections_draft.SETUP_SECTIONS
        assert drafted["commands"] == {
            "commit": ["make", "gate-commit"],
            "ci": ["make", "gate-ci"],
        }
        assert drafted["houseStyle"] == {"version": load_house_style()["version"]}
        assert drafted["tests"]["command_notes"]["lsp"] == [
            {
                "stack": "python",
                "server": "pyright-langserver",
                "install": lsp.STACKS[0].install,
            }
        ]
        assert drafted["tests"]["tiers"]["t1"] == {
            "command": "<command that runs every framework in the tier>"
        }
        assert drafted["tests"]["adapters"][0]["tiers"]["t1"]["selector"] == (
            "<native selector>"
        )
        assert "fill: tests.tiers.t1.command: is missing" in output
        assert "init: drafting from the section template (config outside)" in output
        assert "discovered: make targets: check-paths, gate-ci, gate-commit" in output
        assert (
            "discovered: CI jobs: gate (.github/workflows/gate.yml), Build docs"
            in output
        )
        assert "required checks not read (--offline)" in output
        assert not HARNESS_NAME.search(output), output

    def test_second_run_changes_nothing(self) -> None:
        self.write("Makefile", MAKEFILE)
        assert self.init()[0] == 0
        self.assert_second_run_changes_nothing()

    def test_the_template_is_engine_data_holding_the_setup_sections(self) -> None:
        assert tuple(sections_draft.template()) == sections_draft.SETUP_SECTIONS
        assert (REFERENCE / "guardrails/data/config-template.json").is_file()
        assert not list((REFERENCE / "guardrails/commands/init").glob("*.md"))

    def test_the_repo_config_seeds_the_draft_and_its_values_are_kept(self) -> None:
        values = config(
            checkers={"naming": {"roots": ["src"]}},
            tests={"verification_surface": {"none": True}},
            approvals=[
                *fixture()["approvals"],
                approval("exemption", "verification_surface", "library (ADR-7)"),
            ],
        )
        write_config(self.repo, values)
        settings = self.repo.parent / "settings.json"
        settings.write_text(json.dumps({"naming": {"roots": ["lib"], "extra": 1}}))
        self.write("Makefile", MAKEFILE)
        code, output = self.init("--settings", str(settings))
        assert code == 0, output
        assert "init: drafting from the repo's .agentrails/config.json" in output
        drafted = self.draft()
        assert drafted["commands"] == {
            "commit": ["make", "gate-commit"],
            "ci": ["make", "gate-ci"],
        }
        assert drafted["tests"]["verification_surface"] == {"none": True}
        assert drafted["tests"]["adapters"] == values["tests"]["adapters"]
        assert drafted["checkers"]["naming"] == {"roots": ["src"], "extra": 1}
        assert sections_draft.section_problems(drafted) == []
        assert not [line for line in output.splitlines() if line.startswith("fill:")]

    def test_flags_supply_commands_and_empty_flags_are_refused(self) -> None:
        code, output = self.init(
            "--commit-command", "pre-commit run --all-files", "--ci-command", "just ci"
        )
        assert code == 0, output
        assert self.draft()["commands"] == {
            "commit": ["pre-commit", "run", "--all-files"],
            "ci": ["just", "ci"],
        }
        assert required_fields(output) == []
        for flags in (("--ci-command", " "), ("--commit-command", "")):
            code, output = self.init(*flags)
            assert code == 1
            assert "must not be empty" in output

    def test_an_unknown_flag_is_refused_without_writing_the_draft(self) -> None:
        code, output = self.init("--unknown-flag", "value")
        assert code == 1
        assert "invalid init arguments: " in output
        assert not self.out.exists()

    def test_pre_commit_config_drafts_the_commit_command(self) -> None:
        self.write(
            ".pre-commit-config.yaml",
            "repos:\n  - repo: local\n    hooks:\n      - id: ruff\n",
        )
        code, output = self.init()
        assert code == 0, output
        assert self.draft()["commands"] == {
            "commit": ["pre-commit", "run", "--all-files"]
        }
        assert "discovered: pre-commit hooks: ruff" in output
        assert "confirm: no commands.ci found" in output
        assert required_fields(output) == []

    def test_no_commit_command_found_is_reported_as_required(self) -> None:
        code, output = self.init()
        assert code == 0, output
        assert "required: no commands.commit found; pass --commit-command" in output
        assert "fill: commands.commit: is missing" in output

    def test_a_draft_inside_the_repository_is_refused(self) -> None:
        self.out = self.repo / "drafts/sections.json"
        code, output = self.init()
        assert code == 1
        assert "is inside the repository" in output
        assert not self.out.exists()


class RequiredChecksTests(InitCase):
    """Required checks come from gh; any gh failure is reported, never guessed."""

    def fake_gh(self, script: str) -> None:
        self.executable("gh", script)

    def test_rulesets_and_branch_protection_name_the_required_context(self) -> None:
        self.fake_gh(
            "import json, sys, os\n"
            "assert 'AGENT' not in os.environ\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['repo', 'view']:\n"
            "    print(json.dumps({'nameWithOwner': 'o/n', 'defaultBranchRef': {'name': 'main'}}))\n"
            "elif args[1].endswith('rules/branches/main'):\n"
            "    print(json.dumps([{'type': 'required_status_checks', 'parameters': "
            "{'required_status_checks': [{'context': 'verify'}]}}]))\n"
            "else:\n"
            "    sys.stderr.write('HTTP 404'); sys.exit(1)\n"
        )
        with mock.patch.dict(os.environ, {"AGENT": "harness-agent"}):
            code, output = self.init(offline=False)
        assert code == 0, output
        assert "required checks on the default branch: verify" in output
        assert "ci" not in self.draft(), "the Q&A owns the ci section"

    def test_gh_failure_leaves_required_checks_unknown(self) -> None:
        self.fake_gh("import sys\nsys.stderr.write('not logged in'); sys.exit(4)\n")
        code, output = self.init(offline=False)
        assert code == 0, output
        assert "required checks unknown: gh repo view exited 4: not logged in" in output


def required_fields(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("required:")]


def catalogue_values() -> dict[str, dict[str, Any]]:
    rules = json.loads(CATALOGUE.read_text())["rules"]
    native = ("RUFF.", "MYPY.")
    return {
        rule["id"]: rule["contract"] for rule in rules if rule["id"].startswith(native)
    }


class NativeConfigTests(InitCase):
    def test_data_mirrors_the_catalogue_contracts(self) -> None:
        data = native_config.load_data()
        ruff, contracts = data["ruff"], catalogue_values()
        # A rule may carry a selector, a value or both (a limit and the rule code enforcing it).
        selected = {
            item["selector"] for item in contracts.values() if "selector" in item
        }
        assert set(ruff["lint"]["select"]) == selected
        assert ruff["lint"]["ignore"] == contracts["RUFF.IGNORES"]["ignore"]
        assert ruff["lint.per-file-ignores"] == contracts["RUFF.IGNORES"]["per_file"]
        tables = {
            "ROOT": ruff["root"],
            "FORMAT": ruff["format"],
            "ISORT": ruff["lint.isort"],
            "LIMIT": ruff["lint.pylint"],
        }
        for rule_id, contract in contracts.items():
            if "value" not in contract:
                continue
            family, _, name = rule_id.partition(".")[2].partition(".")
            if rule_id.startswith("MYPY."):
                key = rule_id.removeprefix("MYPY.").removeprefix("SETTING.")
                assert str(data["mypy"][key]) == str(contract["value"]), rule_id
            elif family == "SETTING":
                where = (
                    ruff["lint.mccabe"] if name.startswith("mccabe.") else ruff["root"]
                )
                assert where[name.removeprefix("mccabe.")] == contract["value"], rule_id
            else:
                assert tables[family][name] == contract["value"], rule_id

    def test_proposes_ruff_and_mypy_from_the_repo_and_data(self) -> None:
        self.write("src/target/__init__.py", "")
        code, output = self.init()
        assert code == 0, output
        assert not (self.repo / "ruff.toml").exists()
        assert (
            "native: proposed ruff.toml (apply it to the repository as a fix)" in output
        )
        ruff = tomllib.loads(proposed(output, "ruff.toml"))
        assert ruff["target-version"] == "py311"
        assert ruff["lint"]["isort"]["known-first-party"] == ["target"]
        assert ruff["lint"]["pylint"]["max-args"] == 5
        mypy = proposed(output, "mypy.ini")
        assert "python_version = 3.11" in mypy
        assert "packages = target" in mypy
        assert "mypy_path = src" in mypy
        assert "strict = True" in mypy
        assert "follow_imports = normal" in mypy
        assert "skip" not in mypy

    def test_keeps_existing_tool_configuration(self) -> None:
        self.write("src/target/__init__.py", "")
        self.write(
            "pyproject.toml",
            '[project]\nname = "target"\nrequires-python = ">=3.12"\n\n'
            "[tool.ruff]\nline-length = 100\n",
        )
        self.write("setup.cfg", "[mypy]\nstrict = True\n")
        code, output = self.init()
        assert code == 0, output
        assert "--- begin " not in output
        assert (
            "kept the existing ruff configuration in pyproject.toml [tool.ruff]"
            in output
        )
        assert "kept the existing mypy configuration in setup.cfg [mypy]" in output

    def test_values_init_cannot_derive_leave_configs_unproposed(self) -> None:
        self.write("pyproject.toml", '[project]\nname = "target"\n')
        code, output = self.init()
        assert code == 0, output
        assert "--- begin ruff.toml" not in output
        assert "ruff not proposed; set project.requires-python first" in output

    def test_import_contract_follows_the_config_structure_tiers(self) -> None:
        code, output = self.init()
        assert code == 0, output
        assert "--- begin .importlinter" not in output
        write_config(
            self.repo,
            config(
                structure={
                    "tiers": ["core", "cli"],
                    "import_direction": {"package": "target", "source_root": "src"},
                }
            ),
        )
        code, output = self.init()
        assert code == 0, output
        contract = proposed(output, ".importlinter")
        assert "root_package = target" in contract
        assert "layers =\n    cli\n    core\n" in contract

    def test_proposed_configs_never_name_the_harness(self) -> None:
        # Repositories stay unaware of omp: applying a proposal unchanged adds no harness mention.
        data = native_config.load_data()
        rendered = {
            "ruff.toml": native_config.render_ruff(data, (3, 11), ["target"]),
            "mypy.ini": native_config.render_mypy(data, (3, 11), ["target"], ["src"]),
            ".importlinter": native_config.render_importlinter(
                "target", ["core", "cli"]
            ),
        }
        for name, text in rendered.items():
            assert not re.search(r"\bomp|guardrails", text.lower()), name
            assert not HARNESS_NAME.search(text), name


def language_contracts() -> dict[str, dict[str, Any]]:
    rules = json.loads(CATALOGUE.read_text())["rules"]
    return {
        rule["id"]: rule["contract"]
        for rule in rules
        if rule["id"].startswith(LANGUAGE_PREFIXES)
    }


LANGUAGE_PREFIXES = ("TSC.", "OXLINT.", "GOLANGCI.", "SHELLCHECK.", "SHFMT.")
SHELL_EDITORCONFIG = "root = true\n\n[*]\nindent_style = space\n\n[*.{sh,bash}]\nindent_size = 2\n\n[*.sh]\nswitch_case_indent = true\n"


class LanguageNativeConfigTests(InitCase):
    def test_language_data_mirrors_the_catalogue_contracts(self) -> None:
        data, contracts = native_config.load_data(), language_contracts()
        tsc, oxlint, golangci = (
            data["tsc"]["compilerOptions"],
            data["oxlint"],
            data["golangci"],
        )
        families: dict[str, dict[str, Any]] = {
            family: {} for family in LANGUAGE_PREFIXES
        }
        for rule_id, contract in contracts.items():
            family = next(
                prefix for prefix in LANGUAGE_PREFIXES if rule_id.startswith(prefix)
            )
            families[family][rule_id.removeprefix(family)] = contract
        tsc_rows = {
            key: row["value"] for key, row in families["TSC."].items() if "value" in row
        }
        assert tsc_rows == tsc
        ox = families["OXLINT."]
        assert ox["plugins"]["value"] == oxlint["plugins"]
        assert {
            key.removeprefix("CATEGORY."): row["value"]
            for key, row in ox.items()
            if key.startswith("CATEGORY.")
        } == oxlint["categories"]
        assert {
            row["rule"]: row["value"] for row in ox.values() if "rule" in row
        } == oxlint["rules"]
        for rule in oxlint["rules"]:
            assert rule.split("/")[0] in oxlint["plugins"], rule
        go = families["GOLANGCI."]
        assert go["version"]["value"] == golangci["version"]
        assert go["run.tests"]["value"] == golangci["run"]["tests"]
        assert go["linters.default"]["value"] == golangci["linters"]["default"]
        enabled = {
            key.removeprefix("ENABLE.") for key in go if key.startswith("ENABLE.")
        }
        assert enabled == set(golangci["linters"]["enable"])
        formatters = {
            key.removeprefix("FORMAT.") for key in go if key.startswith("FORMAT.")
        }
        assert formatters == set(golangci["formatters"]["enable"])
        settings = golangci["linters"]["settings"]
        for key, row in go.items():
            if key.startswith("LIMIT.") and row["linter"] != "revive":
                assert settings[row["linter"]][row["setting"]] == row["value"], key
            if key.startswith(("LIMIT.", "RULE.")):
                assert row["linter"] in enabled, key
        revive = {
            item["name"]: item["arguments"] for item in settings["revive"]["rules"]
        }
        assert revive["argument-limit"] == [go["LIMIT.revive.argument-limit"]["value"]]
        assert (
            go["RULE.forbidigo.exec-command"]["value"]
            == settings["forbidigo"]["forbid"]
        )
        assert go["RULE.nolintlint"]["value"] == settings["nolintlint"]
        assert {
            key: row["value"]
            for key, row in families["SHELLCHECK."].items()
            if "value" in row
        } == data["shellcheck"]
        shfmt = families["SHFMT."]
        assert {key: row["value"] for key, row in shfmt.items()} == data["shfmt"][
            "settings"
        ]
        assert {row["section"] for row in shfmt.values()} == {data["shfmt"]["section"]}

    def test_limits_mirror_the_python_thresholds(self) -> None:
        rules = {
            rule["id"]: rule["contract"]
            for rule in json.loads(CATALOGUE.read_text())["rules"]
        }
        for rule_id in (
            "OXLINT.LIMIT.complexity",
            "OXLINT.LIMIT.max-params",
            "OXLINT.LIMIT.max-statements",
            "OXLINT.LIMIT.max-dependencies",
            "GOLANGCI.LIMIT.gocyclo.min-complexity",
            "GOLANGCI.LIMIT.funlen.statements",
            "GOLANGCI.LIMIT.revive.argument-limit",
        ):
            with self.subTest(rule=rule_id):
                contract = rules[rule_id]
                limit = (
                    contract["value"][1]["max"]
                    if rule_id.startswith("OXLINT.")
                    else contract["value"]
                )
                mirrored = rules[contract["mirrors"]]
                expected = mirrored.get(
                    "value", mirrored.get("max_non_stdlib_dependencies")
                )
                assert limit == expected

    def test_proposes_typescript_go_and_shell_configs(self) -> None:
        self.write("package.json", "{}\n")
        self.write("go.mod", "module example.com/shop\n\ngo 1.24\n")
        self.write("scripts/release.sh", "#!/bin/sh\necho release\n")
        before = snapshot(self.repo)
        code, output = self.init()
        assert code == 0, output
        assert snapshot(self.repo) == before
        for name in (
            "tsconfig.json",
            ".oxlintrc.json",
            ".golangci.json",
            ".shellcheckrc",
            ".editorconfig",
        ):
            assert (
                f"native: proposed {name} (apply it to the repository as a fix)"
                in output
            )
        tsconfig = json.loads(proposed(output, "tsconfig.json"))
        assert tsconfig["compilerOptions"]["exactOptionalPropertyTypes"] is True
        golangci = json.loads(proposed(output, ".golangci.json"))
        assert golangci["formatters"]["settings"]["goimports"]["local-prefixes"] == [
            "example.com/shop"
        ]
        assert "enable=check-set-e-suppressed\n" in proposed(output, ".shellcheckrc")
        assert "[*.{sh,bash}]\nindent_style = space\n" in proposed(
            output, ".editorconfig"
        )
        self.assert_second_run_changes_nothing()

    def test_proposed_oxlint_config_is_read_by_the_pinned_oxlint(self) -> None:
        self.write("package.json", "{}\n")
        code, output = self.init()
        assert code == 0, output
        oxlintrc = self.out.parent / "proposed.oxlintrc.json"
        oxlintrc.write_text(proposed(output, ".oxlintrc.json"))
        source = self.write(
            "wide.ts",
            "export function wide(a: number, b: number, c: number, d: number, e: number, f: number): number {\n  return a + b + c + d + e + f;\n}\n",
        )
        result = run_text(
            [
                str(ROOT / "node_modules/.bin/oxlint"),
                "-c",
                str(oxlintrc),
                str(source),
            ],
            timeout=60,
            check=False,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        assert "eslint(max-params)" in result.stdout

    def test_existing_tsconfig_and_editorconfig_report_each_missing_rule(self) -> None:
        self.write(
            "tsconfig.base.json",
            '{"compilerOptions": {"strict": true, "noUnusedLocals": true}}',
        )
        self.write(
            "tsconfig.json",
            '// project config\n{\n  "extends": "./tsconfig.base.json",\n'
            '  "compilerOptions": {"noUncheckedIndexedAccess": true, /* kept */ "noUnusedLocals": false,},\n}\n',
        )
        self.write("scripts/release.sh", "#!/bin/sh\n")
        self.write(".editorconfig", SHELL_EDITORCONFIG)
        code, output = self.init()
        assert code == 0, output
        assert "--- begin tsconfig.json" not in output
        assert "--- begin .editorconfig" not in output
        assert "lacks TSC.strict " not in output
        assert "lacks TSC.noUncheckedIndexedAccess " not in output
        assert (
            "native: tsconfig.json lacks TSC.noUnusedLocals (compilerOptions.noUnusedLocals: true)"
            in output
        )
        assert "native: tsconfig.json lacks TSC.exactOptionalPropertyTypes" in output
        assert "lacks SHFMT.indent_style" not in output
        assert "lacks SHFMT.indent_size" not in output
        assert (
            "native: .editorconfig lacks SHFMT.binary_next_line (binary_next_line = true for shell files)"
            in output
        )
        assert "native: .editorconfig lacks SHFMT.switch_case_indent" in output, (
            ".bash files lack it"
        )

    def test_unreadable_tsconfig_base_is_reported_not_passed(self) -> None:
        self.write("tsconfig.json", '{"extends": "@tsconfig/strictest/tsconfig.json"}')
        code, output = self.init()
        assert code == 0, output
        assert (
            "native: tsc settings not verified; tsconfig.json: extends '@tsconfig/strictest/tsconfig.json' cannot be read"
            in output
        )
        self.write(
            "node_modules/@tsconfig/strictest/tsconfig.json",
            '{"compilerOptions": {"strict": true}}',
        )
        assert "lacks TSC.noImplicitOverride" in self.init()[1]

    def test_existing_linters_are_kept_and_never_doubled(self) -> None:
        self.write("package.json", "{}\n")
        self.write("eslint.config.js", "export default [];\n")
        self.write("go.mod", "module example.com/shop\n")
        self.write(".golangci.yml", 'version: "2"\n')
        self.write("run.bash", "#!/bin/bash\n")
        self.write(".shellcheckrc", "disable=SC1091\n")
        code, output = self.init()
        assert code == 0, output
        for name in (".oxlintrc.json", ".golangci.json", ".shellcheckrc"):
            assert f"--- begin {name}" not in output.splitlines(), name
        assert (
            "native: kept the existing lint configuration in eslint.config.js" in output
        )
        assert (
            "native: kept the existing golangci-lint configuration in .golangci.yml"
            in output
        )
        assert (
            "native: kept the existing shellcheck configuration in .shellcheckrc"
            in output
        )

    def test_go_module_without_a_path_leaves_golangci_unproposed(self) -> None:
        self.write("go.mod", "go 1.24\n")
        code, output = self.init()
        assert code == 0, output
        assert (
            "native: golangci-lint not proposed; go.mod declares no module path"
            in output
        )

    def test_language_proposals_never_name_the_harness(self) -> None:
        data = native_config.load_data()
        for text in (
            native_languages.render_tsconfig(data),
            native_languages.render_oxlint(data),
            native_languages.render_golangci(data, "example.com/shop"),
            native_languages.render_shellcheck(data),
            native_languages.render_editorconfig(data),
        ):
            assert not re.search(r"\bomp|guardrails", text.lower())
            assert not HARNESS_NAME.search(text)


if __name__ == "__main__":
    unittest.main()
