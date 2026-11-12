"""The packaged rule catalogue, its coverage by these tests, toolchain parity and versions.

Catalogue coverage and canaries are the package's own tests: repositories keep no ledger, no
canary fixtures and no validator. Every rule the package proves is named by a test here.
"""

from __future__ import annotations

import ast
import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/skills/setup-repository-guardrails/assets/python-reference"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails import child_env, cli  # noqa: E402
from omp_guardrails.catalogue import rules as catalogue_rules  # noqa: E402
from omp_guardrails.catalogue import toolchain_parity, versions  # noqa: E402

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


class PackagedCatalogueTests(unittest.TestCase):
    """The catalogue ships in the package; its reference rules resolve to emitting modules."""

    def setUp(self) -> None:
        self.catalogue = catalogue_rules.packaged_catalogue()

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
        """Rules the package proves are proven here, once for every repo: each is named by a test."""
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
            if not catalogue_rules.proven_by_package(rule):
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


class RepositoryArtefactTests(unittest.TestCase):
    """The package offers no check that reads repo-held harness artefacts."""

    def test_no_subcommand_reads_a_ledger_hook_gate_wiring_or_canary(self) -> None:
        for retired in ("canaries", "rule-evidence", "rule-coverage", "hook-check", "gate"):
            self.assertNotIn(retired, cli.SUBCOMMANDS)
        package = REFERENCE / "omp_guardrails"
        sources = "".join(path.read_text(encoding="utf-8") for path in package.rglob("*.py"))
        # Built from parts so a repository-wide search for the retired paths finds only code.
        for path in (".omp" + "/", "rule-coverage" + ".json", "guardrail-proposal" + ".json"):
            self.assertNotIn(path, sources)


class ChildEnvironmentTests(unittest.TestCase):
    def test_repository_commands_never_see_harness_variables(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT": "agent-name", "GIT_DIR": "x", "KEEP": "1"}):
            repo_env = child_env.repo_environment()
            clean = child_env.clean_environment(["KEEP"])
        self.assertNotIn("AGENT", repo_env)
        self.assertEqual((repo_env["GIT_DIR"], repo_env["KEEP"]), ("x", "1"))
        self.assertFalse({"AGENT", "GIT_DIR", "KEEP"} & set(clean))
        self.assertNotIn("AGENT", child_env.repo_environment({"AGENT": "a", "PATH": "/bin"}))


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
