"""omp-guardrails init: drafts the repo's personal record and writes nothing into the repo."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/skills/setup-repository-guardrails/assets/python-reference"
SKILL_TEMPLATE = ROOT / "agent/skills/test-guidance/assets/templates/test-profile.md"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails import cli, harness  # noqa: E402
from omp_guardrails.config.house_style import load_house_style  # noqa: E402
from omp_guardrails.config.profile import load_profile  # noqa: E402
from omp_guardrails.init import command, discovery, lsp, native_config  # noqa: E402

CATALOGUE = REFERENCE / "omp_guardrails/data/guardrail-rules.json"
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
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        env=clean_environment(),
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
        which = mock.patch.object(lsp.shutil, "which", return_value=str(self.server))
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

    def gate(self) -> dict:
        return json.loads((self.record / "gate.json").read_text())

    def assert_second_run_changes_nothing(self, *args: str) -> None:
        before = snapshot(self.record)
        code, output = self.init(*args)
        self.assertEqual(code, 0, output)
        self.assertIn("no changes", output)
        self.assertEqual(snapshot(self.record), before)


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
    def test_drafts_gate_and_profile_in_the_record_and_never_touches_the_repo(self) -> None:
        self.write("Makefile", MAKEFILE)
        self.write(".github/workflows/gate.yml", WORKFLOW)
        before = snapshot(self.repo)
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertEqual(snapshot(self.repo), before)
        self.assertEqual(
            self.gate(),
            {
                "schemaVersion": 2,
                "checks": {"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]},
                "pathCheck": ["make", "check-paths"],
                "ci": {"requiredContext": "gate", "runner": "github-actions"},
                "dynaflows": {"enabled": False},
            },
        )
        profile = load_profile(self.record)
        self.assertEqual(profile["house_style"]["version"], load_house_style()["version"])
        self.assertEqual(
            profile["command_notes"]["lsp"],
            [{"stack": "python", "server": "pyright-langserver", "install": lsp.STACKS[0].install}],
        )
        self.assertEqual(profile["tiers"]["t1"]["selector"], "<selector>")
        self.assertIn("discovered: make targets: check-paths, gate-ci, gate-commit", output)
        self.assertIn("discovered: CI jobs: gate (.github/workflows/gate.yml), Build docs", output)
        self.assertIn("required checks not read (--offline)", output)
        self.assertIn("confirm: dynaflows stays off", output)
        self.assertIn("confirm: agentMerge is the owner's to set", output)

    def test_second_run_changes_nothing(self) -> None:
        self.write("Makefile", MAKEFILE)
        self.assertEqual(self.init()[0], 0)
        self.assert_second_run_changes_nothing()

    def test_template_is_the_test_guidance_skill_template(self) -> None:
        self.assertEqual(harness.PROFILE_TEMPLATE, SKILL_TEMPLATE)
        self.assertFalse((REFERENCE / "omp_guardrails/init/test-profile.md").exists())

    def test_recorded_values_and_approvals_are_kept(self) -> None:
        gate = {
            "schemaVersion": 2,
            "checks": {"commit": ["make", "verify"]},
            "agentMerge": "allowed",
            "dynaflows": {"enabled": True},
        }
        self.write_record("gate.json", json.dumps(gate))
        values = {
            "adapter": "pytest",
            "verification_surface": {"none": "library without a UI", "approval": "ADR-7"},
            "guardrails": {"naming": {"roots": ["src"]}},
        }
        self.write_record("profile.md", f"---\n{json.dumps(values)}\n---\n\n# Our profile\n")
        settings = self.repo.parent / "settings.json"
        settings.write_text(json.dumps({"naming": {"roots": ["lib"], "extra": 1}}))
        self.write("Makefile", MAKEFILE)
        code, output = self.init("--settings", str(settings))
        self.assertEqual(code, 0, output)
        drafted = self.gate()
        self.assertEqual(drafted["checks"], {"commit": ["make", "verify"], "ci": ["make", "gate-ci"]})
        self.assertEqual((drafted["agentMerge"], drafted["dynaflows"]), ("allowed", {"enabled": True}))
        profile = load_profile(self.record)
        self.assertEqual(profile["verification_surface"]["approval"], "ADR-7")
        self.assertEqual(profile["guardrails"]["naming"], {"roots": ["src"], "extra": 1})
        self.assertTrue((self.record / "profile.md").read_text().endswith("# Our profile\n"))

    def test_flags_supply_commands_and_ci_and_empty_flags_are_refused(self) -> None:
        code, output = self.init(
            "--commit-command", "pre-commit run --all-files",
            "--ci-command", "just ci",
            "--required-context", "ci",
            "--runner", "ci-gate",
        )
        self.assertEqual(code, 0, output)
        gate = self.gate()
        self.assertEqual(gate["checks"]["ci"], ["just", "ci"])
        self.assertEqual(gate["ci"], {"requiredContext": "ci", "runner": "ci-gate"})
        self.assertNotIn("pathCheck", gate)
        self.assertIn("required: no pathCheck found; pass --path-command", output)
        code, output = self.init("--path-command", "python3 scripts/check-paths.py")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.gate()["pathCheck"], ["python3", "scripts/check-paths.py"])
        self.assertNotIn("required:", output)
        for flags in (("--runner", " "), ("--commit-command", ""), ("--path-command", " ")):
            code, output = self.init(*flags)
            self.assertEqual(code, 1)
            self.assertIn("must not be empty", output)

    def test_pre_commit_config_drafts_the_commit_command(self) -> None:
        self.write(".pre-commit-config.yaml", "repos:\n  - repo: local\n    hooks:\n      - id: ruff\n")
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertEqual(self.gate()["checks"], {"commit": ["pre-commit", "run", "--all-files"]})
        self.assertIn("discovered: pre-commit hooks: ruff", output)
        self.assertIn("confirm: no checks.ci found", output)
        self.assertIn("confirm: no ci found", output)
        self.assertIn("required: no pathCheck found", output)

    def test_no_commit_command_found_is_reported_as_required(self) -> None:
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertIn("required: no checks.commit found; pass --commit-command", output)
        self.assertIn("required: no pathCheck found; pass --path-command", output)

    def test_unreadable_gate_or_unknown_field_is_reported_not_rewritten(self) -> None:
        for gate, named in (({"schemaVersion": 1}, "schemaVersion 2"),
                            ({"schemaVersion": 2, "ciHost": "github"}, "unknown field ciHost must be removed"),
                            ({"schemaVersion": 2, "dynaflowsContract": 3}, "unknown field dynaflowsContract must be removed"),
                            ({"schemaVersion": 2, "checks": {"lint": ["x"]}}, "unknown field checks.lint must be removed")):
            with self.subTest(gate=gate):
                self.write_record("gate.json", json.dumps(gate))
                code, output = self.init()
                self.assertEqual(code, 1)
                self.assertIn("GATE.local-ci-parity", output)
                self.assertIn(named, output)
                self.assertEqual(self.gate(), gate)

    def test_live_record_is_refused_and_the_draft_names_where_the_owner_saves_it(self) -> None:
        live = Path(self.temporary.name).resolve() / "harness/repos"
        with mock.patch.object(harness, "RECORDS", live):
            draft = self.record
            self.record = live / "owner/target"
            code, output = self.init()
            self.assertNotEqual(code, 0)
            self.assertIn("is under the live records root", output)
            self.assertIn("agents never write records", output)
            self.assertFalse(self.record.exists(), "init writes nothing into the live record")
            self.record = draft
            code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertTrue((draft / "gate.json").is_file())
        self.assertIn(f"init: draft record at {draft}. Agents never write the live record", output)
        self.assertIn(f"save it by hand as {live}/<owner>/<name>/", output)

    def test_record_inside_the_repo_is_refused(self) -> None:
        self.record = self.repo / "record"
        self.assertEqual(self.init()[0], 2)
        self.assertFalse(self.record.exists())


class RequiredChecksTests(InitCase):
    """Required checks come from gh; any gh failure is reported, never guessed."""

    def fake_gh(self, script: str) -> None:
        gh = self.executable("gh", script)
        real = discovery.shutil.which
        which = mock.patch.object(
            discovery.shutil, "which", side_effect=lambda name: str(gh) if name == "gh" else real(name)
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
        self.assertEqual(code, 0, output)
        self.assertIn("required checks on the default branch: verify", output)
        self.assertEqual(self.gate()["ci"], {"requiredContext": "verify", "runner": "ci-gate"})

    def test_gh_failure_leaves_required_checks_unknown(self) -> None:
        self.fake_gh("import sys\nsys.stderr.write('not logged in'); sys.exit(4)\n")
        code, output = self.init(offline=False)
        self.assertEqual(code, 0, output)
        self.assertIn("required checks unknown: gh repo view exited 4: not logged in", output)
        self.assertNotIn("ci", self.gate())


class ExtendTests(InitCase):
    """--extend updates an existing record and never creates one."""

    def setUp(self) -> None:
        super().setUp()
        self.write_record("profile.md", '---\n{"adapter": "pytest"}\n---\n')

    def test_extend_updates_an_existing_record_and_never_creates_one(self) -> None:
        self.write_record("gate.json", json.dumps({"schemaVersion": 2}))
        code, output = self.init("--extend")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.gate()["dynaflows"], {"enabled": False})
        (self.record / "gate.json").unlink()
        code, output = self.init("--extend")
        self.assertEqual(code, 1)
        self.assertIn("--extend needs an existing record:gate.json", output)
        self.assertFalse((self.record / "gate.json").exists())

    def test_extend_drafts_the_missing_required_fields(self) -> None:
        self.write_record("gate.json", json.dumps({"schemaVersion": 2, "dynaflows": {"enabled": True}}))
        self.write("Makefile", MAKEFILE)
        code, output = self.init("--extend")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.gate()["checks"]["commit"], ["make", "gate-commit"])
        self.assertEqual(self.gate()["pathCheck"], ["make", "check-paths"])
        self.assertNotIn("required:", output)
        self.assert_second_run_changes_nothing()


class LspTests(InitCase):
    def test_missing_server_is_a_setup_gap_unless_allowed(self) -> None:
        with mock.patch.object(lsp.shutil, "which", return_value=None):
            code, output = self.init()
            self.assertEqual(code, 1)
            self.assertIn("PROFILE.complete", output)
            self.assertIn("pyright-langserver is not on PATH", output)
            code, output = self.init("--allow-missing-lsp")
        self.assertEqual(code, 0, output)
        self.assertIn("allowed by --allow-missing-lsp", output)

    def test_detects_typescript_markers(self) -> None:
        (self.repo / "pyproject.toml").unlink()
        self.write("tsconfig.json", "{}")
        stacks = lsp.detect(self.repo)
        self.assertEqual([stack.server for stack in stacks], ["typescript-language-server"])

    def test_repo_without_stack_records_no_server(self) -> None:
        (self.repo / "pyproject.toml").unlink()
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertNotIn("lsp", load_profile(self.record)["command_notes"])
        self.assertIn("lsp: no pyproject.toml", output)


class CanaryTests(InitCase):
    def test_canary_queries_a_symbol_from_the_repo(self) -> None:
        self.write("src/target/core.py", "import os\n\n\ndef total(values):\n    return 1\n")
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertIn("canary ok: references src/target/core.py:4 total -> 1 locations", output)

    def test_canary_uses_a_canary_module_when_the_repo_has_no_source(self) -> None:
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertIn("canary ok: references in a canary module omp_lsp_canary", output)
        self.assertFalse(list(self.repo.glob("omp_lsp_*")))

    def test_server_that_exits_fails_even_when_missing_servers_are_allowed(self) -> None:
        broken = self.executable("broken-server", "import sys\nsys.exit(97)\n")
        with mock.patch.object(lsp.shutil, "which", return_value=str(broken)):
            code, output = self.init("--allow-missing-lsp")
        self.assertEqual(code, 1)
        self.assertIn("PROFILE.complete", output)
        self.assertIn("failed the canary: the server exited (code 97)", output)

    def test_server_that_never_answers_fails_within_the_time_limit(self) -> None:
        silent = self.executable("silent-server", "import time\ntime.sleep(30)\n")
        with mock.patch.object(lsp.shutil, "which", return_value=str(silent)):
            code, output = self.init("--lsp-timeout", "0.5")
        self.assertEqual(code, 1)
        self.assertIn("no answer to initialize before the time limit", output)


def catalogue_values() -> dict[str, object]:
    rules = json.loads(CATALOGUE.read_text())["rules"]
    native = ("RUFF.", "MYPY.")
    return {rule["id"]: rule["contract"] for rule in rules if rule["id"].startswith(native)}


class NativeConfigTests(InitCase):
    def test_data_mirrors_the_catalogue_contracts(self) -> None:
        data = native_config.load_data()
        ruff, contracts = data["ruff"], catalogue_values()
        # A rule may carry a selector, a value or both (a limit and the rule code enforcing it).
        selected = {item["selector"] for item in contracts.values() if "selector" in item}
        self.assertEqual(set(ruff["lint"]["select"]), selected)
        self.assertEqual(ruff["lint"]["ignore"], contracts["RUFF.IGNORES"]["ignore"])
        self.assertEqual(ruff["lint.per-file-ignores"], contracts["RUFF.IGNORES"]["per_file"])
        tables = {
            "ROOT": ruff["root"],
            "FORMAT": ruff["format"],
            "ISORT": ruff["lint.isort"],
            "LIMIT": ruff["lint.pylint"],
            "PYDOCSTYLE": ruff["lint.pydocstyle"],
        }
        for rule_id, contract in contracts.items():
            if "value" not in contract:
                continue
            family, _, name = rule_id.partition(".")[2].partition(".")
            if rule_id.startswith("MYPY."):
                key = rule_id.removeprefix("MYPY.").removeprefix("SETTING.")
                self.assertEqual(str(data["mypy"][key]), str(contract["value"]), rule_id)
            elif family == "SETTING":
                where = ruff["lint.mccabe"] if name.startswith("mccabe.") else ruff["root"]
                self.assertEqual(where[name.removeprefix("mccabe.")], contract["value"], rule_id)
            else:
                self.assertEqual(tables[family][name], contract["value"], rule_id)

    def test_proposes_ruff_and_mypy_in_the_record_from_the_repo_and_data(self) -> None:
        self.write("src/target/__init__.py", "")
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertFalse((self.repo / "ruff.toml").exists())
        self.assertIn("record:proposed/ruff.toml (apply it to the repository as a fix)", output)
        ruff = tomllib.loads((self.record / "proposed/ruff.toml").read_text())
        self.assertEqual(ruff["target-version"], "py311")
        self.assertEqual(ruff["lint"]["isort"]["known-first-party"], ["target"])
        self.assertEqual(ruff["lint"]["pylint"]["max-args"], 5)
        mypy = (self.record / "proposed/mypy.ini").read_text()
        self.assertIn("python_version = 3.11", mypy)
        self.assertIn("packages = target", mypy)
        self.assertIn("mypy_path = src", mypy)
        self.assertIn("strict = True", mypy)

    def test_keeps_existing_tool_configuration(self) -> None:
        self.write("src/target/__init__.py", "")
        self.write(
            "pyproject.toml",
            '[project]\nname = "target"\nrequires-python = ">=3.12"\n\n'
            "[tool.ruff]\nline-length = 100\n",
        )
        self.write("setup.cfg", "[mypy]\nstrict = True\n")
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertFalse((self.record / "proposed").exists())
        self.assertIn("kept the existing ruff configuration in pyproject.toml [tool.ruff]", output)
        self.assertIn("kept the existing mypy configuration in setup.cfg [mypy]", output)

    def test_values_init_cannot_derive_leave_configs_unproposed(self) -> None:
        self.write("pyproject.toml", '[project]\nname = "target"\n')
        code, output = self.init()
        self.assertEqual(code, 0, output)
        self.assertFalse((self.record / "proposed/ruff.toml").exists())
        self.assertIn("ruff not proposed; set project.requires-python first", output)

    def test_import_contract_follows_recorded_structure_tiers(self) -> None:
        self.assertEqual(self.init()[0], 0)
        self.assertFalse((self.record / "proposed/.importlinter").exists())
        path = self.record / "profile.md"
        text = path.read_text()
        values = load_profile(self.record)
        values["structure"]["tiers"] = ["core", "cli"]
        values["structure"]["import_direction"] = {"package": "target", "source_root": "src"}
        body = text.split("\n---\n", 1)[1]
        path.write_text(f"---\n{json.dumps(values, indent=2)}\n---\n{body}")
        self.assertEqual(self.init()[0], 0)
        contract = (self.record / "proposed/.importlinter").read_text()
        self.assertIn("root_package = target", contract)
        self.assertIn("layers =\n    cli\n    core\n", contract)

    def test_proposed_configs_never_name_the_harness(self) -> None:
        # Repositories stay unaware of omp: applying a proposal unchanged adds no harness mention.
        data = native_config.load_data()
        rendered = {
            "ruff.toml": native_config.render_ruff(data, (3, 11), ["target"]),
            "mypy.ini": native_config.render_mypy(data, (3, 11), ["target"], ["src"]),
            ".importlinter": native_config.render_importlinter("target", ["core", "cli"]),
        }
        for name, text in rendered.items():
            self.assertNotRegex(text.lower(), r"\bomp|guardrails", name)


if __name__ == "__main__":
    unittest.main()
