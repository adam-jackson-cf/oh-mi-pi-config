"""guardrails init: drafts the repo's personal record and writes nothing into the repo."""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine")
)

import pytest

from guardrails.commands import cli
from guardrails.commands.init import harness, lsp, native_config, native_languages
from guardrails.context.config.house_style import load_house_style
from guardrails.context.config.profile import load_profile

pytestmark = pytest.mark.seam

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/dynaflows/engine"
SKILL_TEMPLATE = ROOT / "agent/lib/record/profile.md"

CATALOGUE = REFERENCE / "guardrails/data/guardrail-rules.json"
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
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
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


class InitCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="omp-init-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve() / "repo"
        self.record = Path(self.temporary.name).resolve() / "records/owner/target"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname = "target"\nrequires-python = ">=3.11"\n'
        )
        self.server = self.executable("language-server", FAKE_SERVER)
        which = mock.patch(
            "guardrails.commands.init.lsp.shutil.which", return_value=str(self.server)
        )
        which.start()
        self.addCleanup(which.stop)

    def executable(self, name: str, body: str) -> Path:
        path = self.repo.parent / "bin" / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"#!{sys.executable}\n{body}")
        path.chmod(0o755)
        return path

    def init(self, *args: str, offline: bool = True) -> tuple[int, str]:
        output = io.StringIO()
        location = ["--repo", str(self.repo), "--record", str(self.record)]
        with contextlib.redirect_stdout(output):
            code = cli.main(["init", *location, *(OFFLINE if offline else ()), *args])
        return code, output.getvalue()

    def write(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def write_record(self, name: str, text: str) -> None:
        self.record.mkdir(parents=True, exist_ok=True)
        (self.record / name).write_text(text)

    def gate(self) -> dict[str, Any]:
        gate: dict[str, Any] = json.loads((self.record / "gate.json").read_text())
        return gate

    def assert_second_run_changes_nothing(self, *args: str) -> None:
        before = snapshot(self.record)
        code, output = self.init(*args)
        assert code == 0, output
        assert "no changes" in output
        assert snapshot(self.record) == before


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


class DraftRecordTests(InitCase):
    def test_drafts_gate_and_profile_in_the_record_and_never_touches_the_repo(
        self,
    ) -> None:
        self.write("Makefile", MAKEFILE)
        self.write(".github/workflows/gate.yml", WORKFLOW)
        before = snapshot(self.repo)
        code, output = self.init()
        assert code == 0, output
        assert snapshot(self.repo) == before
        assert self.gate() == {
            "schemaVersion": 2,
            "checks": {"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]},
            "ci": {"requiredContext": "gate", "runner": "github-actions"},
            "dynaflows": {"enabled": False},
        }
        profile = load_profile(self.record)
        assert profile["house_style"]["version"] == load_house_style()["version"]
        assert set(profile["house_style"]) == {"version"}
        assert set(profile["guardrails"]["exceptions"][0]) == {"rule", "path", "reason"}
        assert profile["command_notes"]["lsp"] == [
            {
                "stack": "python",
                "server": "pyright-langserver",
                "install": lsp.STACKS[0].install,
            }
        ]
        assert profile["tiers"]["t1"] == {
            "command": "<command that runs every framework in the tier>"
        }
        assert profile["adapters"][0]["tiers"]["t1"]["selector"] == "<native selector>"
        assert "discovered: make targets: check-paths, gate-ci, gate-commit" in output
        assert (
            "discovered: CI jobs: gate (.github/workflows/gate.yml), Build docs"
            in output
        )
        assert "required checks not read (--offline)" in output
        assert "confirm: dynaflows is off" in output
        assert "turns on when the owner asks for it in a session" in output
        assert "confirm: agentMerge is the owner's to set" in output
        assert "required: ownerOnlyPaths is the owner's to set" in output
        assert "ownerOnlyPaths" not in self.gate(), (
            "init never drafts the owner-only paths"
        )

    def test_recorded_owner_only_paths_are_kept_and_satisfy_the_requirement(
        self,
    ) -> None:
        owned = ["scripts/checks/dependency-audit-exceptions.json"]
        gate = {"schemaVersion": 2, "ownerOnlyPaths": owned}
        self.write_record("gate.json", json.dumps(gate))
        self.write("Makefile", MAKEFILE)
        code, output = self.init()
        assert code == 0, output
        assert self.gate()["ownerOnlyPaths"] == owned
        assert "ownerOnlyPaths" not in output

    def test_second_run_changes_nothing(self) -> None:
        self.write("Makefile", MAKEFILE)
        assert self.init()[0] == 0
        self.assert_second_run_changes_nothing()

    def test_template_is_the_test_guidance_skill_template(self) -> None:
        assert harness.PROFILE_TEMPLATE == SKILL_TEMPLATE
        assert not (REFERENCE / "guardrails/commands/init/test-profile.md").exists()

    def test_recorded_values_and_approvals_are_kept(self) -> None:
        gate = {
            "schemaVersion": 2,
            "checks": {"commit": ["make", "verify"]},
            "agentMerge": "allowed",
            "dynaflows": {"enabled": True},
        }
        self.write_record("gate.json", json.dumps(gate))
        values = {
            "adapters": [
                {
                    "name": "pytest",
                    "tiers": {"t1": {"selector": "fast", "location": "t"}},
                }
            ],
            "verification_surface": {
                "none": "library without a UI",
                "approval": "ADR-7",
            },
            "guardrails": {"naming": {"roots": ["src"]}},
        }
        self.write_record(
            "profile.md", f"---\n{json.dumps(values)}\n---\n\n# Our profile\n"
        )
        settings = self.repo.parent / "settings.json"
        settings.write_text(json.dumps({"naming": {"roots": ["lib"], "extra": 1}}))
        self.write("Makefile", MAKEFILE)
        code, output = self.init("--settings", str(settings))
        assert code == 0, output
        drafted = self.gate()
        assert drafted["checks"] == {
            "commit": ["make", "verify"],
            "ci": ["make", "gate-ci"],
        }
        assert (drafted["agentMerge"], drafted["dynaflows"]) == (
            "allowed",
            {"enabled": True},
        )
        profile = load_profile(self.record)
        assert profile["verification_surface"]["approval"] == "ADR-7"
        assert [entry["name"] for entry in profile["adapters"]] == ["pytest"]
        assert profile["guardrails"]["naming"] == {"roots": ["src"], "extra": 1}
        assert (self.record / "profile.md").read_text().endswith("# Our profile\n")

    def test_flags_supply_commands_and_ci_and_empty_flags_are_refused(self) -> None:
        code, output = self.init(
            "--commit-command",
            "pre-commit run --all-files",
            "--ci-command",
            "just ci",
            "--required-context",
            "ci",
            "--runner",
            "ci-gate",
        )
        assert code == 0, output
        gate = self.gate()
        assert gate["checks"]["ci"] == ["just", "ci"]
        assert gate["ci"] == {"requiredContext": "ci", "runner": "ci-gate"}
        assert required_fields(output) == ["ownerOnlyPaths"], (
            "only the owner's field remains"
        )
        for flags in (("--runner", " "), ("--commit-command", "")):
            code, output = self.init(*flags)
            assert code == 1
            assert "must not be empty" in output

    def test_repo_path_command_flag_is_refused_without_writing_the_record(self) -> None:
        code, output = self.init("--path-command", "python3 scripts/check_paths.py")
        assert code == 1
        assert "invalid init arguments: --offline --path-command" in output
        assert not self.record.exists()

    def test_pre_commit_config_drafts_the_commit_command(self) -> None:
        self.write(
            ".pre-commit-config.yaml",
            "repos:\n  - repo: local\n    hooks:\n      - id: ruff\n",
        )
        code, output = self.init()
        assert code == 0, output
        assert self.gate()["checks"] == {"commit": ["pre-commit", "run", "--all-files"]}
        assert "discovered: pre-commit hooks: ruff" in output
        assert "confirm: no checks.ci found" in output
        assert "confirm: no ci found" in output
        assert required_fields(output) == ["ownerOnlyPaths"]

    def test_no_commit_command_found_is_reported_as_required(self) -> None:
        code, output = self.init()
        assert code == 0, output
        assert "required: no checks.commit found; pass --commit-command" in output

    def test_unreadable_gate_or_unknown_field_is_reported_not_rewritten(self) -> None:
        for gate, named in (
            ({"schemaVersion": 3}, "schemaVersion 2"),
            (
                {"schemaVersion": 2, "otherField": "github"},
                "unknown field otherField must be removed",
            ),
            (
                {"schemaVersion": 2, "unknownField": 3},
                "unknown field unknownField must be removed",
            ),
            (
                {"schemaVersion": 2, "checks": {"lint": ["x"]}},
                "unknown field checks.lint must be removed",
            ),
        ):
            with self.subTest(gate=gate):
                self.write_record("gate.json", json.dumps(gate))
                code, output = self.init()
                assert code == 1
                assert "GATE.local-ci-parity" in output
                assert named in output
                assert self.gate() == gate

    def test_live_record_can_be_written_with_dynaflows_off(self) -> None:
        live = Path(self.temporary.name).resolve() / "harness/repos"
        with mock.patch.object(harness, "RECORDS", live):
            self.record = live / "owner/target"
            code, output = self.init()
        assert code == 0, output
        assert (self.record / "gate.json").is_file()
        assert self.gate()["dynaflows"] == {"enabled": False}

    def test_dynaflows_ask_writes_the_live_record_with_dynaflows_on(self) -> None:
        live = Path(self.temporary.name).resolve() / "harness/repos"
        with mock.patch.object(harness, "RECORDS", live):
            self.record = live / "owner/target"
            code, output = self.init(
                "--enable-dynaflows", "--commit-command", "make check"
            )
            assert code == 0, output
            assert self.gate()["dynaflows"] == {"enabled": True}
            assert f"init: record at {self.record}" in output
            assert "confirm: dynaflows is off" not in output
            self.record = live / "owner"
            code, output = self.init("--enable-dynaflows")
            assert code != 0
            assert "must be a repo's record" in output
            assert not (live / "owner/gate.json").exists()

    def test_record_inside_the_repo_is_refused(self) -> None:
        self.record = self.repo / "record"
        assert self.init()[0] == 2
        assert not self.record.exists()


class RequiredChecksTests(InitCase):
    """Required checks come from gh; any gh failure is reported, never guessed."""

    def fake_gh(self, script: str) -> None:
        gh = self.executable("gh", script)
        real = shutil.which
        which = mock.patch(
            "guardrails.commands.init.discovery.shutil.which",
            side_effect=lambda name: str(gh) if name == "gh" else real(name),
        )
        which.start()
        self.addCleanup(which.stop)

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
            code, output = self.init("--runner", "ci-gate", offline=False)
        assert code == 0, output
        assert "required checks on the default branch: verify" in output
        assert self.gate()["ci"] == {"requiredContext": "verify", "runner": "ci-gate"}

    def test_gh_failure_leaves_required_checks_unknown(self) -> None:
        self.fake_gh("import sys\nsys.stderr.write('not logged in'); sys.exit(4)\n")
        code, output = self.init(offline=False)
        assert code == 0, output
        assert "required checks unknown: gh repo view exited 4: not logged in" in output
        assert "ci" not in self.gate()


def required_fields(output: str) -> list[str]:
    """The gate fields init reports as required, by the field each ``required:`` line names."""
    fields = []
    for line in output.splitlines():
        if line.startswith("required:"):
            fields.append("ownerOnlyPaths" if "ownerOnlyPaths" in line else line)
    return fields


class ExistingRecordTests(InitCase):
    """Init fills the absent fields of an existing record."""

    def setUp(self) -> None:
        super().setUp()
        adapters = [
            {"name": "pytest", "tiers": {"t1": {"selector": "fast", "location": "t"}}}
        ]
        self.write_record(
            "profile.md", f"---\n{json.dumps({'adapters': adapters})}\n---\n"
        )

    def test_drafts_the_missing_required_fields(self) -> None:
        self.write_record(
            "gate.json",
            json.dumps({"schemaVersion": 2, "dynaflows": {"enabled": True}}),
        )
        self.write("Makefile", MAKEFILE)
        code, output = self.init()
        assert code == 0, output
        assert self.gate()["checks"]["commit"] == ["make", "gate-commit"]
        assert required_fields(output) == ["ownerOnlyPaths"], (
            "only the owner's field remains"
        )
        self.assert_second_run_changes_nothing()


class LspTests(InitCase):
    def test_missing_server_is_a_setup_gap_unless_allowed(self) -> None:
        with mock.patch("guardrails.commands.init.lsp.shutil.which", return_value=None):
            code, output = self.init()
            assert code == 1
            assert "PROFILE.complete" in output
            assert "pyright-langserver is not on PATH" in output
            code, output = self.init("--allow-missing-lsp")
        assert code == 0, output
        assert "allowed by --allow-missing-lsp" in output

    def test_detects_typescript_markers(self) -> None:
        (self.repo / "pyproject.toml").unlink()
        self.write("tsconfig.json", "{}")
        stacks = lsp.detect(self.repo)
        assert [stack.server for stack in stacks] == ["typescript-language-server"]

    def test_typescript_six_keeps_the_javascript_language_server(self) -> None:
        self.write("package.json", "{}")
        self.write("node_modules/typescript/package.json", '{"version": "6.0.3"}')
        stack = lsp.detect(self.repo)[1]
        assert stack.server == "typescript-language-server"
        assert stack.arguments == ("--stdio",)

    def test_typescript_seven_queries_the_installed_native_server(self) -> None:
        self.write("package.json", "{}")
        self.write("node_modules/typescript/package.json", '{"version": "7.0.2"}')
        native = self.repo / "node_modules/typescript/bin/tsc"
        self.write(
            "node_modules/typescript/bin/tsc",
            f"#!{sys.executable}\nassert __import__('sys').argv[1:] == ['--lsp', '--stdio']\n"
            + FAKE_SERVER,
        )
        native.chmod(0o755)
        stack = lsp.detect(self.repo)[1]
        assert stack.server == str(native)
        with mock.patch(
            "guardrails.commands.init.lsp.shutil.which",
            side_effect=lambda server: server,
        ):
            findings, lines = lsp.readiness(self.repo, [stack], allow_missing=False)
        assert findings == []
        assert "canary ok: references in a canary module" in lines[0]

    def test_repo_without_stack_records_no_server(self) -> None:
        (self.repo / "pyproject.toml").unlink()
        code, output = self.init()
        assert code == 0, output
        assert "lsp" not in load_profile(self.record)["command_notes"]
        assert "lsp: no pyproject.toml" in output


class CanaryTests(InitCase):
    def test_canary_queries_a_symbol_from_the_repo(self) -> None:
        self.write(
            "src/target/core.py", "import os\n\n\ndef total(values):\n    return 1\n"
        )
        code, output = self.init()
        assert code == 0, output
        assert (
            "canary ok: references src/target/core.py:4 total -> 1 locations" in output
        )

    def test_canary_uses_a_canary_module_when_the_repo_has_no_source(self) -> None:
        code, output = self.init()
        assert code == 0, output
        assert "canary ok: references in a canary module omp_lsp_canary" in output
        assert not list(self.repo.glob("omp_lsp_*"))

    def test_server_that_exits_fails_even_when_missing_servers_are_allowed(
        self,
    ) -> None:
        broken = self.executable("broken-server", "import sys\nsys.exit(97)\n")
        with mock.patch(
            "guardrails.commands.init.lsp.shutil.which", return_value=str(broken)
        ):
            code, output = self.init("--allow-missing-lsp")
        assert code == 1
        assert "PROFILE.complete" in output
        assert "failed the canary: the server exited (code 97)" in output

    def test_server_that_never_answers_fails_within_the_time_limit(self) -> None:
        silent = self.executable("silent-server", "import time\ntime.sleep(30)\n")
        with mock.patch(
            "guardrails.commands.init.lsp.shutil.which", return_value=str(silent)
        ):
            code, output = self.init("--lsp-timeout", "0.5")
        assert code == 1
        assert "no answer to initialize before the time limit" in output


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

    def test_proposes_ruff_and_mypy_in_the_record_from_the_repo_and_data(self) -> None:
        self.write("src/target/__init__.py", "")
        code, output = self.init()
        assert code == 0, output
        assert not (self.repo / "ruff.toml").exists()
        assert (
            "record:proposed/ruff.toml (apply it to the repository as a fix)" in output
        )
        ruff = tomllib.loads((self.record / "proposed/ruff.toml").read_text())
        assert ruff["target-version"] == "py311"
        assert ruff["lint"]["isort"]["known-first-party"] == ["target"]
        assert ruff["lint"]["pylint"]["max-args"] == 5
        mypy = (self.record / "proposed/mypy.ini").read_text()
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
        assert not (self.record / "proposed").exists()
        assert (
            "kept the existing ruff configuration in pyproject.toml [tool.ruff]"
            in output
        )
        assert "kept the existing mypy configuration in setup.cfg [mypy]" in output

    def test_values_init_cannot_derive_leave_configs_unproposed(self) -> None:
        self.write("pyproject.toml", '[project]\nname = "target"\n')
        code, output = self.init()
        assert code == 0, output
        assert not (self.record / "proposed/ruff.toml").exists()
        assert "ruff not proposed; set project.requires-python first" in output

    def test_import_contract_follows_recorded_structure_tiers(self) -> None:
        assert self.init()[0] == 0
        assert not (self.record / "proposed/.importlinter").exists()
        path = self.record / "profile.md"
        text = path.read_text()
        values = load_profile(self.record)
        values["structure"]["tiers"] = ["core", "cli"]
        values["structure"]["import_direction"] = {
            "package": "target",
            "source_root": "src",
        }
        body = text.split("\n---\n", 1)[1]
        path.write_text(f"---\n{json.dumps(values, indent=2)}\n---\n{body}")
        assert self.init()[0] == 0
        contract = (self.record / "proposed/.importlinter").read_text()
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

    def test_proposes_typescript_go_and_shell_configs_in_the_record(self) -> None:
        self.write("package.json", "{}\n")
        self.write("go.mod", "module example.com/shop\n\ngo 1.24\n")
        self.write("scripts/release.sh", "#!/bin/sh\necho release\n")
        before = snapshot(self.repo)
        code, output = self.init()
        assert code == 0, output
        assert snapshot(self.repo) == before
        proposed = self.record / "proposed"
        for name in (
            "tsconfig.json",
            ".oxlintrc.json",
            ".golangci.json",
            ".shellcheckrc",
            ".editorconfig",
        ):
            assert (
                f"record:proposed/{name} (apply it to the repository as a fix)"
                in output
            )
        tsconfig = json.loads((proposed / "tsconfig.json").read_text())
        assert tsconfig["compilerOptions"]["exactOptionalPropertyTypes"] is True
        golangci = json.loads((proposed / ".golangci.json").read_text())
        assert golangci["formatters"]["settings"]["goimports"]["local-prefixes"] == [
            "example.com/shop"
        ]
        assert (
            "enable=check-set-e-suppressed\n"
            in (proposed / ".shellcheckrc").read_text()
        )
        assert (
            "[*.{sh,bash}]\nindent_style = space\n"
            in (proposed / ".editorconfig").read_text()
        )
        self.assert_second_run_changes_nothing()

    def test_proposed_oxlint_config_is_read_by_the_pinned_oxlint(self) -> None:
        self.write("package.json", "{}\n")
        assert self.init()[0] == 0
        source = self.write(
            "wide.ts",
            "export function wide(a: number, b: number, c: number, d: number, e: number, f: number): number {\n  return a + b + c + d + e + f;\n}\n",
        )
        result = subprocess.run(
            [
                str(ROOT / "node_modules/.bin/oxlint"),
                "-c",
                str(self.record / "proposed/.oxlintrc.json"),
                str(source),
            ],
            capture_output=True,
            text=True,
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
        assert not (self.record / "proposed/tsconfig.json").exists()
        assert not (self.record / "proposed/.editorconfig").exists()
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
            assert not (self.record / "proposed" / name).exists(), name
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


if __name__ == "__main__":
    unittest.main()
