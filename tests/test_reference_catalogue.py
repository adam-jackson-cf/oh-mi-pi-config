"""Catalogue checkers of the guardrails reference: gate, hook, rule coverage, canaries, parity."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/skills/setup-repository-guardrails/assets/python-reference"
FIXTURES = ROOT / "tests/fixtures/reference/catalogue"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails import cli  # noqa: E402
from omp_guardrails.catalogue import (  # noqa: E402
    canaries,
    gate_runner,
    hook_installation,
    rule_coverage,
    rule_evidence,
    toolchain_parity,
    versions,
)

EMPTY_PROFILE = "---\n{}\n---\n"
# Tool rules whose values tests/test_reference_init.py NativeConfigTests checks.
NATIVE_PREFIXES = ("RUFF.", "MYPY.")


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
        env=env,
    )


def rules_of(findings: list) -> list[str]:
    return [finding.rule_id for finding in findings]


class RepoCase(unittest.TestCase):
    """Each test gets a fresh git repository with a commit identity."""

    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-reference-catalogue-")
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name).resolve()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Catalogue Test")
        git(self.repo, "config", "user.email", "catalogue-test@example.invalid")
        git(self.repo, "config", "commit.gpgsign", "false")

    def write(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_gate(self, checks: dict, **extra: object) -> None:
        gate = {"schemaVersion": 2, "checks": checks, **extra}
        self.write(".omp/gate.json", json.dumps(gate))

    def run_cli(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = cli.main([args[0], "--repo", str(self.repo), *args[1:]])
        return status, output.getvalue()


def exit_argv(status: int) -> list[str]:
    return [sys.executable, "-c", f"raise SystemExit({status})"]


class GateRunnerTests(RepoCase):
    def test_passing_check_reports_nothing(self) -> None:
        self.write_gate({"ci": exit_argv(0)})
        self.assertEqual(gate_runner.run(self.repo, {}, ["ci"]), [])

    def test_failing_check_is_a_failure_propagation_finding(self) -> None:
        self.write_gate({"ci": exit_argv(3)})
        findings = gate_runner.run(self.repo, {}, ["ci"])
        self.assertEqual(rules_of(findings), ["TEST.failure-propagation"])
        self.assertIn("checks.ci exited 3", findings[0].message)

    def test_cli_gate_exits_non_zero_when_the_check_fails(self) -> None:
        self.write(".omp/test-profile.md", EMPTY_PROFILE)
        self.write_gate({"commit": exit_argv(5), "ci": exit_argv(0)})
        self.assertEqual(self.run_cli("gate", "ci")[0], 0)
        status, output = self.run_cli("gate", "commit")
        self.assertEqual(status, 1)
        self.assertIn("TEST.failure-propagation", output)

    def test_broken_gate_configuration_is_a_parity_finding(self) -> None:
        cases = {
            "missing file": None,
            "invalid json": "{",
            "wrong schema": json.dumps({"schemaVersion": 1, "checks": {"ci": ["true"]}}),
            "empty argv": json.dumps({"schemaVersion": 2, "checks": {"ci": []}}),
            "string argv": json.dumps({"schemaVersion": 2, "checks": {"ci": "make ci"}}),
            "undeclared check": json.dumps({"schemaVersion": 2, "checks": {"commit": ["true"]}}),
        }
        for name, text in cases.items():
            with self.subTest(name):
                gate = self.repo / ".omp/gate.json"
                gate.unlink(missing_ok=True)
                if text is not None:
                    self.write(".omp/gate.json", text)
                findings = gate_runner.run(self.repo, {}, ["ci"])
                self.assertEqual(rules_of(findings), ["GATE.local-ci-parity"])

    def test_missing_runner_and_bad_selection_are_parity_findings(self) -> None:
        self.write_gate({"ci": ["omp-guardrails-test-runner-that-does-not-exist"]})
        self.assertEqual(rules_of(gate_runner.run(self.repo, {}, ["ci"])), ["GATE.local-ci-parity"])
        for argv in ([], ["ci", "commit"], ["--ci"]):
            with self.subTest(argv=argv):
                self.assertEqual(
                    rules_of(gate_runner.run(self.repo, {}, argv)), ["GATE.local-ci-parity"]
                )

    def test_checks_run_without_the_callers_git_context(self) -> None:
        probe = "import os, sys; sys.exit(any(k.startswith('GIT_') for k in os.environ))"
        self.write_gate({"ci": [sys.executable, "-c", probe]})
        with mock.patch.dict(os.environ, {"GIT_DIR": "/elsewhere", "GIT_INDEX_FILE": "x"}):
            self.assertEqual(gate_runner.run(self.repo, {}, ["ci"]), [])

    def test_unset_env_setting_removes_named_variables(self) -> None:
        probe = "import os, sys; sys.exit('AGENT_MARKER' in os.environ)"
        self.write_gate({"ci": [sys.executable, "-c", probe]})
        profile = {"guardrails": {"gate": {"unset_env": ["AGENT_MARKER"]}}}
        with mock.patch.dict(os.environ, {"AGENT_MARKER": "1"}):
            self.assertEqual(len(gate_runner.run(self.repo, {}, ["ci"])), 1)
            self.assertEqual(gate_runner.run(self.repo, profile, ["ci"]), [])

    def test_wiring_checks_declared_argv_path_check_and_entry_lines(self) -> None:
        self.write_gate({"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]})
        self.write(".github/workflows/gate.yml", "jobs:\n  gate:\n    steps:\n      - run: make\n")
        profile = {
            "guardrails": {
                "gate": {
                    "checks": {"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]},
                    "require_path_check": True,
                    "files": [
                        {
                            "path": ".github/workflows/gate.yml",
                            "lines": ["- run: omp-guardrails gate ci"],
                        },
                        {"path": ".githooks/pre-commit", "rule": "GATE.pre-commit", "lines": ["x"]},
                    ],
                }
            }
        }
        findings = gate_runner.run(self.repo, profile, ["wiring"])
        self.assertEqual(
            sorted(rules_of(findings)),
            ["GATE.local-ci-parity", "GATE.path-check", "GATE.pre-commit"],
        )
        self.write_gate(
            {"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]},
            pathCheck=["python3", "scripts/check-paths.py"],
        )
        self.write(".github/workflows/gate.yml", "steps:\n  - run: omp-guardrails gate ci\n")
        self.write(".githooks/pre-commit", "x\n")
        self.assertEqual(gate_runner.run(self.repo, profile, ["wiring"]), [])
        profile["guardrails"]["gate"]["checks"]["ci"] = ["make", "other"]
        self.assertEqual(
            rules_of(gate_runner.run(self.repo, profile, ["wiring"])), ["GATE.local-ci-parity"]
        )

    def test_wiring_checks_the_ci_object_and_refuses_the_retired_ci_host(self) -> None:
        cases = {
            "retired ciHost": ({"ciHost": "github"}, "ciHost is retired"),
            "missing runner": ({"ci": {"requiredContext": "gate"}}, "non-empty requiredContext"),
            "empty context": (
                {"ci": {"requiredContext": " ", "runner": "ci-gate"}},
                "non-empty requiredContext",
            ),
        }
        for name, (fields, message) in cases.items():
            with self.subTest(name):
                self.write_gate({"ci": ["true"]}, **fields)
                findings = gate_runner.run(self.repo, {}, ["wiring"])
                self.assertEqual(rules_of(findings), ["GATE.local-ci-parity"])
                self.assertIn(message, findings[0].message)
        self.write_gate({"ci": ["true"]}, ci={"requiredContext": "gate", "runner": "ci-gate"})
        self.assertEqual(gate_runner.run(self.repo, {}, ["wiring"]), [])


def hook_script(body: str) -> str:
    return f"#!/bin/sh\n{body}\n"


GATE_HOOK = hook_script(
    f'PYTHONPATH="{REFERENCE}" exec "{sys.executable}" -m omp_guardrails.cli gate commit'
)


class HookInstallationTests(RepoCase):
    def install(self, relative: str, text: str) -> None:
        path = self.write(relative, text)
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def test_missing_hook_is_a_pre_commit_finding(self) -> None:
        findings = hook_installation.run(self.repo, {}, [])
        self.assertEqual(rules_of(findings), ["GATE.pre-commit"])
        self.assertIn("no executable pre-commit hook", findings[0].message)

    def test_non_executable_hook_is_a_finding(self) -> None:
        self.write(".git/hooks/pre-commit", GATE_HOOK)
        self.assertEqual(rules_of(hook_installation.run(self.repo, {}, [])), ["GATE.pre-commit"])

    def test_hook_in_git_dir_that_runs_the_gate_propagates_failure(self) -> None:
        self.install(".git/hooks/pre-commit", GATE_HOOK)
        self.assertEqual(hook_installation.run(self.repo, {}, []), [])

    def test_tracked_hooks_folder_is_followed_through_core_hooks_path(self) -> None:
        self.install(".githooks/pre-commit", GATE_HOOK)
        git(self.repo, "config", "core.hooksPath", ".githooks")
        self.assertEqual(hook_installation.run(self.repo, {}, []), [])
        self.write(".omp/test-profile.md", EMPTY_PROFILE)
        status, output = self.run_cli("hook-check")
        self.assertEqual((status, output.strip()), (0, "hook-check: ok"))

    def test_hook_that_ignores_the_gate_status_is_a_finding(self) -> None:
        self.install(".git/hooks/pre-commit", hook_script("exit 0"))
        findings = hook_installation.run(self.repo, {}, [])
        self.assertEqual(rules_of(findings), ["GATE.pre-commit"])
        self.assertIn("did not block", findings[0].message)

    def test_hook_that_always_fails_is_a_finding(self) -> None:
        self.install(".git/hooks/pre-commit", hook_script("exit 1"))
        findings = hook_installation.run(self.repo, {}, [])
        self.assertIn("still blocked", findings[0].message)

    def test_hook_runtime_files_are_copied_and_wiring_text_is_required(self) -> None:
        self.write("tools/run-gate.sh", GATE_HOOK)
        self.install(".git/hooks/pre-commit", hook_script("exec sh tools/run-gate.sh"))
        options = {"copy": ["tools/run-gate.sh"]}
        self.assertEqual(rules_of(hook_installation.run(self.repo, {}, [])), ["GATE.pre-commit"])
        profile = {"guardrails": {"hook_installation": options}}
        self.assertEqual(hook_installation.run(self.repo, profile, []), [])
        options["required_text"] = {"tools/run-gate.sh": ["gate commit", "always_run: true"]}
        findings = hook_installation.run(self.repo, profile, [])
        self.assertEqual(rules_of(findings), ["GATE.pre-commit"])
        self.assertIn("always_run: true", findings[0].message)

    def test_hook_check_never_changes_the_callers_checkout(self) -> None:
        self.install(".git/hooks/pre-commit", GATE_HOOK)
        before = git(self.repo, "status", "--porcelain").stdout
        hook_installation.run(self.repo, {}, [])
        self.assertEqual(git(self.repo, "status", "--porcelain").stdout, before)
        self.assertFalse((self.repo / ".omp/gate.json").exists())


SYNTHETIC_CATALOGUE = {
    "schema_version": 1,
    "rules": [
        {"id": "REPO.always", "kind": "process", "applicability": "always"},
        {
            "id": "REPO.hook",
            "kind": "bespoke",
            "applicability": "always",
            "evidence_kind": "git_hook",
        },
        {"id": "REPO.optional", "kind": "process", "applicability": "conditional"},
        {"id": "TOOL.setting", "kind": "tool", "applicability": "always"},
        {
            "id": "PKG.checked",
            "kind": "bespoke",
            "applicability": "always",
            "reference": [{"subcommand": "naming", "module": "omp_guardrails.structure.naming"}],
        },
    ],
}
CHECKER = (
    "import pathlib, sys\n"
    "rule, fixture = sys.argv[1:]\n"
    "if pathlib.Path(fixture).name.startswith('bad'):\n"
    "    print(rule)\n"
    "    raise SystemExit(1)\n"
)


class RuleEvidenceCase(RepoCase):
    """A repo with one working checker script and a ledger over a synthetic catalogue."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(
            rule_evidence, "packaged_catalogue", return_value=SYNTHETIC_CATALOGUE
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.write("check.py", CHECKER)
        self.write("runner.sh", "python check.py\n")
        self.write("ci.yml", "runner.sh\n")
        self.write("good.txt", "good\n")
        self.write("bad.txt", "bad\n")
        self.rows = [self.row("REPO.always"), self.hook_row("REPO.hook")]

    @staticmethod
    def row(rule_id: str, **extra: object) -> dict:
        return {
            "id": rule_id,
            "implementation_paths": ["check.py"],
            "local_runner": "runner.sh",
            "local_marker": "check.py",
            "ci_runner": "ci.yml",
            "ci_marker": "runner.sh",
            "canary": {
                "command": [sys.executable, "check.py", rule_id, "{fixture}"],
                "passing": "good.txt",
                "violating": "bad.txt",
                "diagnostic": rule_id,
                "timeout_seconds": 10,
            },
            **extra,
        }

    def hook_row(self, rule_id: str, code: str | None = None) -> dict:
        row = self.row(rule_id, evidence_kind="git_hook")
        del row["ci_runner"], row["ci_marker"]
        script = code or f"print('{rule_id}: installed')"
        row["hook_install_check"] = {
            "command": [sys.executable, "-c", script],
            "diagnostic": f"{rule_id}: installed",
        }
        return row

    def ledger(self, rows: list | None = None) -> None:
        rules = self.rows if rows is None else rows
        self.write(".omp/rule-coverage.json", json.dumps({"schema_version": 2, "rules": rules}))

    def evidence(self, profile: dict | None = None) -> list:
        return rule_evidence.run(self.repo, profile or {}, [])

    def coverage(self) -> list:
        return rule_coverage.run(self.repo, {}, [])


class RuleEvidenceTests(RuleEvidenceCase):
    def test_complete_ledger_of_repo_checkers_passes(self) -> None:
        self.ledger()
        self.assertEqual(self.evidence(), [])

    def test_repo_without_repo_checkers_needs_no_ledger(self) -> None:
        catalogue = {"schema_version": 1, "rules": SYNTHETIC_CATALOGUE["rules"][2:]}
        with mock.patch.object(rule_evidence, "packaged_catalogue", return_value=catalogue):
            self.assertEqual(self.evidence(), [])
            self.assertEqual(self.coverage(), [])

    def test_absent_ledger_reports_each_always_applicable_repo_checker(self) -> None:
        self.assertEqual(sorted(rules_of(self.evidence())), ["REPO.always", "REPO.hook"])

    def test_rules_the_package_proves_take_no_ledger_row(self) -> None:
        for rule_id in ("TOOL.setting", "PKG.checked"):
            with self.subTest(rule_id):
                self.ledger([*self.rows, self.row(rule_id)])
                findings = self.evidence()
                self.assertEqual(rules_of(findings), [rule_id])
                self.assertIn("proven by the pinned omp-guardrails package", findings[0].message)

    def test_conditional_repo_checker_without_row_is_not_adopted(self) -> None:
        self.ledger()
        self.assertNotIn("REPO.optional", rules_of(self.evidence()))
        self.ledger([*self.rows, self.row("REPO.optional")])
        self.assertEqual(self.evidence(), [])

    def test_local_rules_must_be_marked(self) -> None:
        self.ledger([*self.rows, self.row("LOCAL.seam")])
        findings = self.evidence()
        self.assertEqual(rules_of(findings), ["LOCAL.seam"])
        self.assertIn('"local": true', findings[0].message)
        self.ledger([*self.rows, self.row("LOCAL.seam", local=True)])
        self.assertEqual(self.evidence(), [])

    def test_row_defects_are_reported_under_the_rule_they_concern(self) -> None:
        always, hook = self.rows
        no_canary = {key: value for key, value in always.items() if key != "canary"}
        cases = {
            "duplicate row": ([always, always, hook], "duplicate"),
            "wrong kind": ([{**always, "evidence_kind": "git_hook"}, hook], "evidence kind"),
            "missing implementation": (
                [{**always, "implementation_paths": ["absent.py"]}, hook],
                "missing or outside",
            ),
            "absolute path": (
                [{**always, "local_runner": str(self.repo / "runner.sh")}, hook],
                "repo-relative",
            ),
            "runner lacks marker": ([{**always, "ci_marker": "other"}, hook], "ci_marker"),
            "no canary": ([no_canary, hook], "canary is missing"),
            "canary without fixture": (
                [{**always, "canary": {**always["canary"], "command": ["true"]}}, hook],
                "{fixture}",
            ),
            "canary timeout": (
                [{**always, "canary": {**always["canary"], "timeout_seconds": 600}}, hook],
                "timeout_seconds",
            ),
        }
        for name, (rows, text) in cases.items():
            with self.subTest(name):
                self.ledger(rows)
                findings = self.evidence()
                self.assertEqual(rules_of(findings), ["REPO.always"])
                self.assertIn(text, findings[0].message)

    def test_hook_rows_need_an_installation_check_not_a_ci_runner(self) -> None:
        always, hook = self.rows
        del hook["hook_install_check"]
        self.ledger([always, hook])
        findings = self.evidence()
        self.assertEqual(rules_of(findings), ["REPO.hook"])
        self.assertIn("hook_install_check", findings[0].message)

    def test_old_or_unreadable_ledgers_are_findings(self) -> None:
        for name, text in (
            ("schema 1", json.dumps({"schema_version": 1, "rules": []})),
            ("not json", "{"),
            ("row without id", json.dumps({"schema_version": 2, "rules": [{}]})),
        ):
            with self.subTest(name):
                self.write(".omp/rule-coverage.json", text)
                self.assertEqual(rules_of(self.evidence()), [rule_evidence.LEDGER_RULE])

    def test_ledger_path_is_a_setting(self) -> None:
        self.write("policy/ledger.json", json.dumps({"schema_version": 2, "rules": self.rows}))
        profile = {"guardrails": {"rule_coverage": {"ledger": "policy/ledger.json"}}}
        self.assertEqual(self.evidence(profile), [])

    def test_rule_evidence_runs_no_canary(self) -> None:
        self.write("check.py", "raise SystemExit(0)\n")
        self.ledger()
        self.assertEqual(self.evidence(), [])


class RuleCoverageTests(RuleEvidenceCase):
    def test_live_canaries_and_hook_checks_pass(self) -> None:
        self.ledger()
        self.assertEqual(self.coverage(), [])
        self.write(".omp/test-profile.md", EMPTY_PROFILE)
        status, output = self.run_cli("rule-coverage")
        self.assertEqual(status, 0, output)

    def test_coverage_reports_ledger_defects_too(self) -> None:
        self.ledger([self.rows[0]])
        self.assertEqual(rules_of(self.coverage()), ["REPO.hook"])

    def test_violating_canary_must_fail_with_its_diagnostic(self) -> None:
        for name, code, text in (
            ("passes everything", "raise SystemExit(0)\n", "violating canary passed"),
            ("silent failure", "raise SystemExit(1)\n", "compliant canary failed"),
            (
                "wrong diagnostic",
                CHECKER.replace("print(rule)", "print('other')"),
                "did not print its diagnostic",
            ),
        ):
            with self.subTest(name):
                self.write("check.py", code)
                self.ledger()
                findings = self.coverage()
                self.assertEqual(sorted(set(rules_of(findings))), ["REPO.always", "REPO.hook"])
                self.assertIn(text, findings[0].message)

    def test_hook_installation_must_succeed_and_report_its_proof(self) -> None:
        for code, text in (
            ("raise SystemExit(1)", "hook installation check failed"),
            ("print('unrelated')", "did not print its diagnostic"),
        ):
            with self.subTest(code=code):
                self.ledger([self.rows[0], self.hook_row("REPO.hook", code)])
                findings = self.coverage()
                self.assertEqual(rules_of(findings), ["REPO.hook"])
                self.assertIn(text, findings[0].message)

    def test_canary_timeout_is_a_finding(self) -> None:
        row = self.row("REPO.always")
        row["canary"]["command"] = [sys.executable, "-c", "import time; time.sleep(5)", "{fixture}"]
        row["canary"]["timeout_seconds"] = 1
        self.ledger([row, self.rows[1]])
        findings = self.coverage()
        self.assertEqual(rules_of(findings), ["REPO.always"])
        self.assertIn("could not complete", findings[0].message)


class PackagedCatalogueTests(unittest.TestCase):
    """The catalogue ships in the package; its reference rules resolve to emitting modules."""

    def setUp(self) -> None:
        self.catalogue = rule_evidence.packaged_catalogue()

    def test_catalogue_ships_as_package_data(self) -> None:
        self.assertEqual(self.catalogue["schema_version"], 1)
        ids = [rule["id"] for rule in self.catalogue["rules"]]
        self.assertEqual(len(ids), len(set(ids)))
        for rule in self.catalogue["rules"]:
            with self.subTest(rule=rule["id"]):
                self.assertIn(rule["kind"], {"bespoke", "process", "tool"})
                self.assertIn(rule["applicability"], {"always", "conditional"})
                self.assertTrue(isinstance(rule["summary"], str) and rule["summary"])

    def test_references_name_subcommands_whose_modules_emit_the_rule(self) -> None:
        for rule in self.catalogue["rules"]:
            for item in rule.get("reference", []):
                subcommand, module = item["subcommand"], item["module"]
                with self.subTest(rule=rule["id"], module=module):
                    self.assertEqual(set(item), {"subcommand", "module"})
                    self.assertIn(subcommand, cli.SUBCOMMANDS)
                    if subcommand == "adapter":
                        self.assertTrue(module.startswith(cli.ADAPTER_PACKAGE + "."))
                    else:
                        self.assertIn(module, cli.SUBCOMMANDS[subcommand].modules)
                    sources = module_sources(module)
                    self.assertTrue(sources, f"missing module {module}")
                    sources += [
                        source
                        for imported in reference_imports(sources)
                        for source in module_sources(imported)
                    ]
                    self.assertTrue(
                        any(rule["id"] in source.read_text(encoding="utf-8") for source in sources),
                        f"{module} never names {rule['id']}",
                    )

    def test_every_package_proven_rule_is_exercised_by_the_skill_tests(self) -> None:
        """Rules repos no longer canary are proven here, once: each is named by a test."""
        tests = ROOT / "tests"
        sources = [*tests.glob("test_reference_*.py"), *(tests / "fixtures/reference").rglob("*")]
        texts = "".join(
            path.read_text(encoding="utf-8")
            for path in sorted(sources)
            if path.is_file() and path.suffix in {".py", ".json", ".md", ".xml"}
        )
        conformance = REFERENCE / "omp_guardrails/adapters/pytest/conformance"
        texts += "".join(path.read_text(encoding="utf-8") for path in conformance.glob("*.json"))
        for rule in self.catalogue["rules"]:
            if not rule_evidence.proven_by_package(rule):
                continue
            with self.subTest(rule=rule["id"]):
                # RUFF.* and MYPY.* tool rules: init writes the native config the tool
                # enforces, and NativeConfigTests checks it against every such catalogue row.
                native = rule["kind"] == "tool" and rule["id"].startswith(NATIVE_PREFIXES)
                self.assertTrue(native or rule["id"] in texts, f"no skill test names {rule['id']}")


def module_sources(module: str) -> list[Path]:
    """Source files of a reference module: one ``.py`` file, or a package's code and rules."""
    path = REFERENCE.joinpath(*module.split("."))
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix in {".py", ".yml", ".json"})
    return [path.with_suffix(".py")] if path.with_suffix(".py").is_file() else []


def reference_imports(sources: list[Path]) -> set[str]:
    """``omp_guardrails`` modules imported by the given Python sources."""
    imported: set[str] = set()
    for source in sources:
        if source.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "omp_guardrails."
            ):
                imported.add(node.module)
    return imported


class CanaryTests(RepoCase):
    def canary(self, name: str) -> str:
        return str(FIXTURES / "canaries" / name)

    def test_violating_gate_fixture_reports_its_rule(self) -> None:
        findings = canaries.run(self.repo, {}, [self.canary("gate-failure.json")])
        self.assertEqual(rules_of(findings), ["TEST.failure-propagation"])

    def test_compliant_gate_fixture_reports_nothing(self) -> None:
        self.assertEqual(canaries.run(self.repo, {}, [self.canary("gate-success.json")]), [])

    def test_native_command_fixture_fails_on_the_planted_violation(self) -> None:
        findings = canaries.run(self.repo, {}, [self.canary("native-command.json")])
        self.assertEqual(rules_of(findings), ["NAME.conventions"])

    def test_cli_exit_status_matches_the_validator_canary_contract(self) -> None:
        self.write(".omp/test-profile.md", EMPTY_PROFILE)
        self.assertEqual(self.run_cli("canaries", self.canary("gate-success.json"))[0], 0)
        status, output = self.run_cli("canaries", self.canary("gate-failure.json"))
        self.assertEqual(status, 1)
        self.assertIn("TEST.failure-propagation", output)

    def test_only_findings_for_the_fixture_rule_count(self) -> None:
        fixture = {
            "rule": "GATE.pre-commit",
            "check": {"subcommand": "gate", "argv": ["ci"]},
            "files": {".omp/gate.json": json.dumps({"schemaVersion": 2, "checks": {}})},
        }
        path = self.write("canary.json", json.dumps(fixture))
        self.assertEqual(canaries.run(self.repo, {}, [str(path)]), [])

    def test_profile_overrides_and_copied_files_reach_the_check(self) -> None:
        self.write(".github/workflows/gate.yml", "- run: omp-guardrails gate ci\n")
        fixture = {
            "rule": "GATE.local-ci-parity",
            "check": {"subcommand": "gate", "argv": ["wiring"]},
            "copy": [".github/workflows/gate.yml"],
            "files": {".omp/gate.json": json.dumps({"schemaVersion": 2, "checks": {}})},
            "profile": {
                "guardrails": {
                    "gate": {
                        "files": [
                            {
                                "path": ".github/workflows/gate.yml",
                                "lines": ["- run: omp-guardrails gate ci"],
                            }
                        ]
                    }
                }
            },
        }
        path = self.write("canary.json", json.dumps(fixture))
        self.assertEqual(canaries.run(self.repo, {}, [str(path)]), [])
        fixture["copy"] = []
        path.write_text(json.dumps(fixture))
        self.assertEqual(
            rules_of(canaries.run(self.repo, {}, [str(path)])), ["GATE.local-ci-parity"]
        )

    def test_base_and_delete_give_diff_checks_a_staged_change(self) -> None:
        fixture = {
            "rule": "TEST.failure-propagation",
            "check": {"subcommand": "gate", "argv": ["ci"]},
            "base": {"src/kept.txt": "a\n", "src/removed.txt": "b\n"},
            "delete": ["src/removed.txt"],
            "files": {
                ".omp/gate.json": json.dumps(
                    {
                        "schemaVersion": 2,
                        "checks": {"ci": ["git", "diff", "--cached", "--quiet", "--", "src"]},
                    }
                )
            },
        }
        path = self.write("canary.json", json.dumps(fixture))
        self.assertEqual(
            rules_of(canaries.run(self.repo, {}, [str(path)])), ["TEST.failure-propagation"]
        )

    def test_malformed_or_escaping_fixtures_are_harness_findings(self) -> None:
        cases = {
            "not json": "{",
            "no rule": json.dumps({"check": {"subcommand": "gate"}}),
            "two checks": json.dumps(
                {"rule": "R", "check": {"subcommand": "gate", "command": ["true"]}}
            ),
            "bad expect": json.dumps({"rule": "R", "expect": "maybe", "check": {"command": ["t"]}}),
            "recursive": json.dumps({"rule": "R", "check": {"subcommand": "canaries"}}),
            "escape": json.dumps(
                {"rule": "R", "check": {"command": ["true"]}, "files": {"../out.txt": "x"}}
            ),
            "missing copy": json.dumps(
                {"rule": "R", "check": {"command": ["true"]}, "copy": ["absent.toml"]}
            ),
        }
        for name, text in cases.items():
            with self.subTest(name):
                path = self.write("canary.json", text)
                self.assertEqual(
                    rules_of(canaries.run(self.repo, {}, [str(path)])), ["CANARY.fixture"]
                )
        self.assertFalse((self.repo.parent / "out.txt").exists())

    def test_configured_fixtures_are_judged_against_their_expectations(self) -> None:
        for name in ("gate-failure.json", "gate-success.json", "native-command.json"):
            shutil.copy(FIXTURES / "canaries" / name, self.write(f".omp/canaries/{name}", ""))
        profile = {"guardrails": {"canaries": {"fixtures": [".omp/canaries/*.json"]}}}
        self.assertEqual(canaries.run(self.repo, profile, []), [])
        broken = json.loads((FIXTURES / "canaries/gate-failure.json").read_text())
        broken["expect"] = "pass"
        self.write(".omp/canaries/gate-failure.json", json.dumps(broken))
        swapped = json.loads((FIXTURES / "canaries/gate-success.json").read_text())
        swapped["expect"] = "fail"
        self.write(".omp/canaries/gate-success.json", json.dumps(swapped))
        messages = sorted(finding.message for finding in canaries.run(self.repo, profile, []))
        self.assertEqual(len(messages), 2)
        self.assertTrue(messages[0].startswith("compliant canary failed"))
        self.assertEqual(messages[1], "violating canary passed")

    def test_no_configured_fixtures_is_a_finding(self) -> None:
        self.assertEqual(rules_of(canaries.run(self.repo, {}, [])), ["CANARY.fixture"])


PYPROJECT = """\
[project]
name = "sample-app"
version = "1.2.0"
dependencies = ["uv==0.9.2", "click>=8,<9"]

[project.optional-dependencies]
dev = ["pip-audit==2.9.0"]
docs = ["mkdocs~=1.6"]
"""

LOCK = """\
version = 1

[[package]]
name = "sample-app"
version = "1.2.0"

[[package]]
name = "uv"
version = "0.9.2"

[[package]]
name = "click"
version = "8.1.7"

[[package]]
name = "pip-audit"
version = "2.9.0"

[[package]]
name = "MkDocs"
version = "1.6.1"
"""

MAKEFILE = """\
.PHONY: env-setup
env-setup:
\t@uv sync --project . --all-extras \\
\t  --frozen
"""

README = """\
# Sample

```sh
make env-setup
```
"""


class ToolchainParityTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("pyproject.toml", PYPROJECT)
        self.write("uv.lock", LOCK)
        self.write("Makefile", MAKEFILE)
        self.write("README.md", README)
        self.write(".github/workflows/ci.yml", "    - run: uv sync --all-extras --locked\n")
        self.uv = {
            "project": "pyproject.toml",
            "lock": "uv.lock",
            "installer_sources": ["Makefile", ".github/workflows/*.yml"],
            "guidance": ["README.md"],
            "exact_uv_pin": True,
        }

    def parity(self, options: dict, *argv: str) -> list:
        profile = {"guardrails": {"toolchain_parity": options}}
        return toolchain_parity.run(self.repo, profile, list(argv))

    def test_consistent_uv_toolchain_passes(self) -> None:
        self.assertEqual(self.parity({"uv": self.uv}), [])

    def test_partial_installer_extras_are_an_inventory_finding(self) -> None:
        self.write(".github/workflows/ci.yml", "    - run: uv sync --extra dev --locked\n")
        findings = self.parity({"uv": self.uv})
        self.assertIn("PARITY.dependency-inventory", rules_of(findings))
        self.assertEqual(findings[0].path, ".github/workflows/ci.yml:1")

    def test_installers_for_other_projects_are_not_compared(self) -> None:
        self.write(".github/workflows/ci.yml", "run: uv sync --project tools --extra x --locked\n")
        self.assertNotIn("PARITY.dependency-inventory", rules_of(self.parity({"uv": self.uv})))

    def test_documented_installs_must_match_a_maintained_installer(self) -> None:
        self.write("README.md", "```bash\nuv sync --extra dev\nmake install-everything\n```\n")
        findings = self.parity({"uv": self.uv})
        self.assertEqual(rules_of(findings), ["PARITY.install-guidance"] * 2)

    def test_lock_drift_is_a_lock_version_finding(self) -> None:
        self.write("uv.lock", LOCK.replace('version = "8.1.7"', 'version = "9.0.0"'))
        findings = self.parity({"uv": self.uv})
        self.assertEqual(rules_of(findings), ["PARITY.lock-version"])
        self.assertIn("click", findings[0].message)

    def test_uv_pin_must_be_exact_and_locked(self) -> None:
        self.write("pyproject.toml", PYPROJECT.replace("uv==0.9.2", "uv>=0.9"))
        self.assertIn("exact uv", self.parity({"uv": self.uv})[0].message)
        self.write("pyproject.toml", PYPROJECT.replace("uv==0.9.2", "uv==0.9.3"))
        self.assertIn("PARITY.lock-version", rules_of(self.parity({"uv": self.uv})))

    def test_python_pins_must_match_each_other_and_the_interpreter(self) -> None:
        running = platform.python_version()
        self.write(".python-version", f"{running}\n")
        self.write("app/.python-version", f"{running}\n")
        options = {"python_pins": [".python-version", "app/.python-version"]}
        self.assertEqual(self.parity(options), [])
        self.write("app/.python-version", "3.0.0\n")
        self.assertEqual(rules_of(self.parity(options)), ["GATE.local-ci-parity"] * 2)
        (self.repo / "app/.python-version").unlink()
        self.assertIn("missing", self.parity(options)[0].message)

    def test_prettier_must_be_exactly_locked_and_never_fetched_by_npx(self) -> None:
        version = "3.6.2"
        self.write("package.json", json.dumps({"devDependencies": {"prettier": version}}))
        lock = {
            "packages": {
                "": {"devDependencies": {"prettier": version}},
                "node_modules/prettier": {"version": version, "integrity": "sha", "resolved": "u"},
            }
        }
        self.write("package-lock.json", json.dumps(lock))
        options = {"prettier": {"version": version, "npx_sources": ["Makefile"]}}
        self.assertEqual(self.parity(options), [])
        self.write("Makefile", "fmt:\n\tnpx prettier --write .\n")
        self.assertEqual([f.path for f in self.parity(options)], ["Makefile"])
        self.assertEqual(rules_of(self.parity(options)), ["PRETTIER.version"])
        options["prettier"]["version"] = "3.7.0"
        self.assertIn("package.json", [f.path for f in self.parity(options)])

    def test_unconfigured_sections_never_pass_silently(self) -> None:
        self.assertEqual(rules_of(self.parity({})), ["GATE.local-ci-parity"])
        audit = self.parity({"uv": self.uv}, "audit")
        self.assertEqual(rules_of(audit), ["GATE.local-ci-parity"])

    def test_unreadable_inputs_are_findings(self) -> None:
        (self.repo / "uv.lock").unlink()
        self.assertEqual(rules_of(self.parity({"uv": self.uv})), ["PARITY.lock-version"])


def report_command(report: object, status: int = 0) -> list[str]:
    printed = json.dumps(json.dumps(report))
    script = f"import sys; sys.stdin.read(); print({printed}); sys.exit({status})"
    return [sys.executable, "-c", script]


CLEAN_REPORT = {"dependencies": [{"name": "click", "version": "8.1.7", "vulns": []}]}
VULNERABLE_REPORT = {
    "dependencies": [
        {
            "name": "click",
            "version": "8.1.7",
            "vulns": [{"id": "PYSEC-1", "fix_versions": ["8.1.8"]}],
        }
    ]
}


class DependencyAuditTests(RepoCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("pyproject.toml", PYPROJECT)
        self.write("uv.lock", LOCK)
        self.audit = {
            "export_command": [sys.executable, "-c", "print('click==8.1.7')"],
            "audit_command": report_command(CLEAN_REPORT),
            "timeout_s": 60,
        }

    def audit_findings(self, *argv: str) -> list:
        profile = {"guardrails": {"toolchain_parity": {"audit": self.audit}}}
        return toolchain_parity.run(self.repo, profile, ["audit", *argv])

    def test_local_audit_runs_only_for_dependency_changes(self) -> None:
        self.audit["audit_command"] = report_command(VULNERABLE_REPORT, 1)
        self.assertEqual(self.audit_findings("src/app.py"), [])
        self.assertEqual(rules_of(self.audit_findings("uv.lock")), ["SECURITY.dependency-audit"])
        git(self.repo, "add", "pyproject.toml")
        self.assertEqual(len(self.audit_findings()), 1)

    def test_ci_audit_always_runs_and_reports_each_vulnerability(self) -> None:
        self.assertEqual(self.audit_findings("--ci"), [])
        self.audit["audit_command"] = report_command(VULNERABLE_REPORT, 1)
        findings = self.audit_findings("--ci")
        self.assertIn("PYSEC-1", findings[0].message)
        self.assertIn("8.1.8", findings[0].fix)

    def test_untrustworthy_reports_fail_closed(self) -> None:
        cases = {
            "invalid": "not json",
            "empty": {"dependencies": []},
            "skipped": {
                "dependencies": [{"name": "a", "version": "1", "vulns": [], "skip_reason": "x"}]
            },
            "malformed vuln": {"dependencies": [{"name": "a", "version": "1", "vulns": [{}]}]},
        }
        for name, report in cases.items():
            with self.subTest(name):
                self.assertTrue(
                    toolchain_parity.audit_report_problem(
                        report if isinstance(report, str) else json.dumps(report)
                    )
                )
        self.audit["export_command"] = [sys.executable, "-c", "pass"]
        self.assertIn("export failed", self.audit_findings("--ci")[0].message)
        self.audit["export_command"] = ["omp-guardrails-missing-exporter"]
        self.assertIn("audit tool failed", self.audit_findings("--ci")[0].message)
        del self.audit["audit_command"]
        self.assertIn("audit_command", self.audit_findings("--ci")[0].message)

    def test_auditor_pin_must_be_exact_and_locked(self) -> None:
        self.write("pyproject.toml", PYPROJECT.replace("pip-audit==2.9.0", "pip-audit>=2"))
        self.assertIn("exact pin", self.audit_findings("--ci")[0].message)
        self.write("pyproject.toml", PYPROJECT.replace("pip-audit==2.9.0", "pip-audit==2.9.1"))
        self.assertIn("differs", self.audit_findings("--ci")[0].message)


class VersionMatchingTests(unittest.TestCase):
    def test_pep_440_ordering(self) -> None:
        key = versions.version_key
        ordered = ["1.0.dev1", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.post1.dev1", "1.0.post1"]
        self.assertEqual(sorted(ordered, key=key), ordered)
        self.assertEqual(key("1.0"), key("1.0.0"))
        self.assertEqual(key("1!0.1") > key("2.0"), True)

    def test_requirement_parsing_and_specifiers(self) -> None:
        requirement = versions.parse_requirement('Foo_Bar[x] >=1.2, <2 ; python_version > "3"')
        self.assertEqual(requirement.name, "foo-bar")
        self.assertTrue(requirement.admits("1.9.9"))
        self.assertFalse(requirement.admits("2.0"))
        cases = {
            ("pkg==1.4.*", "1.4.7"): True,
            ("pkg==1.4.*", "1.5"): False,
            ("pkg!=1.4.*", "1.5"): True,
            ("pkg~=1.4.2", "1.4.9"): True,
            ("pkg~=1.4.2", "1.5.0"): False,
            ("pkg~=1.4", "1.9"): True,
            ("pkg===1.0", "1.0"): True,
            ("pkg>1.0", "1.0.post1"): True,
            ("pkg", "0.0.1"): True,
            ("pkg @ https://example.invalid/pkg.whl", "9"): True,
        }
        for (text, version), admitted in cases.items():
            with self.subTest(text=text, version=version):
                self.assertEqual(versions.parse_requirement(text).admits(version), admitted)

    def test_unsupported_input_raises(self) -> None:
        for text in ("", "pkg >> 1", "pkg~=1"):
            with self.subTest(text=text), self.assertRaises(versions.VersionError):
                versions.parse_requirement(text).admits("1.0")


if __name__ == "__main__":
    unittest.main()
