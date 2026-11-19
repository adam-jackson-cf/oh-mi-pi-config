"""The packaged rule catalogue, its coverage by these tests, toolchain parity and versions.

Every rule the package proves is named by a test here.
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
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/engine"))


from guardrails import child_env, cli
from guardrails.catalogue import (
    rules as catalogue_rules,
    toolchain_parity,
    versions,
)

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/extensions/dynaflows/engine"

# Tool rules whose values tests/test_reference_init.py NativeConfigTests and
# LanguageNativeConfigTests check against the data init writes.
NATIVE_PREFIXES = ("RUFF.", "MYPY.", "TSC.", "OXLINT.", "GOLANGCI.", "SHELLCHECK.", "SHFMT.")


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


def rules_of(findings: list[Any]) -> list[str]:
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
        assert self.catalogue["schema_version"] == 1
        ids = [rule["id"] for rule in self.catalogue["rules"]]
        assert len(ids) == len(set(ids))
        for rule in self.catalogue["rules"]:
            with self.subTest(rule=rule["id"]):
                assert rule["kind"] in {"bespoke", "process", "tool"}
                assert rule["applicability"] in {"always", "conditional"}
                assert isinstance(rule["summary"], str)
                assert rule["summary"]

    def test_path_check_contract_is_a_harness_subcommand_over_record_settings(self) -> None:
        rule = next(rule for rule in self.catalogue["rules"] if rule["id"] == "GATE.path-check")
        contract = rule["contract"]
        assert contract["configuration"] == "record:profile.md"
        assert contract["harness_subcommand"] == "guardrails path-check"
        assert "required_command_field" not in contract
        assert contract["changed_paths_appended_as_arguments"]
        assert contract["success_exit_code"] == 0
        assert contract["violation_exit_nonzero"]

    def test_naming_contract_limits_events_to_new_path_segments(self) -> None:
        rules = {rule["id"]: rule["contract"] for rule in self.catalogue["rules"]}
        assert "content edits do not trigger naming" in rules["NAME.conventions"]["event_scope"]
        assert "legacy parent" in rules["NAME.conventions"]["segment_scope"]
        assert "full scans" in rules["NAME.test-files"]["event_scope"]
        exceptions = rules["PROFILE.complete"]["guardrail_exceptions"]
        assert exceptions["field"] == "guardrails.exceptions"
        assert exceptions["required_fields"] == ["rule", "path", "reason"]
        assert exceptions["optional_fields"] == ["check"]
        assert exceptions["unknown_fields_rejected"]

    def test_references_name_subcommands_whose_modules_emit_the_rule(self) -> None:
        for rule in self.catalogue["rules"]:
            for item in rule.get("reference", []):
                subcommand, module = item["subcommand"], item["module"]
                with self.subTest(rule=rule["id"], module=module):
                    assert set(item) == {"subcommand", "module"}
                    assert subcommand in cli.SUBCOMMANDS
                    if subcommand == "adapter":
                        assert module.startswith(cli.ADAPTER_PACKAGE + ".")
                    else:
                        assert module in cli.SUBCOMMANDS[subcommand].modules
                    sources = module_sources(module)
                    assert sources, f"missing module {module}"
                    sources += [
                        source
                        for imported in reference_imports(sources)
                        for source in module_sources(imported)
                    ]
                    assert any(rule["id"] in source.read_text(encoding="utf-8") for source in sources), f"{module} never names {rule['id']}"

    def test_every_package_proven_rule_is_exercised_by_the_skill_tests(self) -> None:
        """Rules the package proves are proven here, once for every repo: each is named by a test."""
        tests = ROOT / "tests"
        sources = [*tests.glob("test_reference_*.py"), *(tests / "fixtures/reference").rglob("*")]
        texts = "".join(
            path.read_text(encoding="utf-8")
            for path in sorted(sources)
            if path.is_file() and path.suffix in {".py", ".json", ".md", ".xml"}
        )
        conformance = REFERENCE / "guardrails/adapters/pytest/conformance"
        texts += "".join(path.read_text(encoding="utf-8") for path in conformance.glob("*.json"))
        for rule in self.catalogue["rules"]:
            if not catalogue_rules.proven_by_package(rule):
                continue
            with self.subTest(rule=rule["id"]):
                # Native tool rules: init writes the native config the tool enforces, and
                # the native config tests check it against every such catalogue row.
                native = rule["kind"] == "tool" and rule["id"].startswith(NATIVE_PREFIXES)
                assert native or rule["id"] in texts, f"no skill test names {rule['id']}"


class DependencyAuditContractTests(unittest.TestCase):
    """SECURITY.dependency-audit belongs to the repo's checker; the catalogue states its contract."""

    def setUp(self) -> None:
        rows = catalogue_rules.packaged_catalogue()["rules"]
        self.row = next(rule for rule in rows if rule["id"] == "SECURITY.dependency-audit")
        self.contract = self.row["contract"]

    def test_the_repo_checker_owns_the_rule_and_the_harness_runs_no_copy(self) -> None:
        assert "reference" not in self.row
        assert not catalogue_rules.proven_by_package(self.row)
        assert self.row["applicability"] == "always"
        assert self.contract["setup_outcomes"] == ["repo", "grow"]
        assert self.contract["harness_copy_forbidden"]
        assert "dependency-audit" not in "".join(cli.SUBCOMMANDS)

    def test_scope_is_whole_repo_and_fails_closed(self) -> None:
        assert self.contract["scope_source"] == "git ls-files --cached --others --exclude-standard"
        for key in (
            "unknown_lock_format_is_failure",
            "manifest_with_dependencies_and_no_lock_is_failure",
            "staged_file_trigger_forbidden",
            "every_advisory_blocks",
            "severity_scoring_in_checker_forbidden",
            "exclusion_by_glob_ecosystem_or_package_forbidden",
        ):
            with self.subTest(key=key):
                assert self.contract[key] is True
        assert self.contract["scheduled_run_by_default"] is False

    def test_the_advisory_line_names_its_fields_in_the_stated_order(self) -> None:
        tokens = self.contract["advisory_line"].split(" ")
        assert tokens[0] == "SECURITY.dependency-audit"
        fields = [token.split("=", 1)[0] for token in tokens[1:]]
        assert fields == self.contract["advisory_fields_in_order"]
        failure = self.contract["failure_line"].split(" ")
        assert [token.split("=", 1)[0] for token in failure[1:4]] == ["failure", "path", "reason"]

    def test_exceptions_are_owner_only_dated_and_only_for_unavailable_fixes(self) -> None:
        exceptions = self.contract["exceptions"]
        assert exceptions["allowed_only_for"] == ["advisory_with_no_fix_version", "fixed_release_blocked_by_pinned_dependent"]
        assert exceptions["entry_fields"] == ["advisory", "package", "version", "lock", "reason", "exposure", "expires"]
        assert exceptions["blocked_fix_fields"] == ["blocked_by", "constraint", "constraint_source"]
        for rule in (
            "suppresses_only_the_exact_advisory_package_version_and_lock",
            "expired_entry_reports_the_advisory_again_and_a_failure",
            "entry_matching_no_reported_advisory_is_failure",
            "entry_for_an_advisory_with_an_unblocked_fix_is_failure",
            "malformed_entry_is_failure",
        ):
            with self.subTest(rule=rule):
                assert exceptions[rule] is True
        for requirement in (
            "same lock", "constraint must parse", "target the advisory package",
            "exclude every reported fixed version", "absolute HTTPS URL",
            "missing blocking fields", "missing or changed blocker", "any compatible fixed version",
        ):
            with self.subTest(requirement=requirement):
                assert requirement in exceptions["blocked_fix_validation"]
        assert "never fetch" in exceptions["upstream_evidence_attestation"]
        assert "unknown or missing fields fail" in exceptions["entry_validation"]
        assert exceptions["expires_format"] == "YYYY-MM-DD"
        assert "today's UTC date" in exceptions["expires_validation"]
        assert exceptions["agents_never_add_or_edit"] is True
        assert "owner_only_paths" in exceptions["file"]


def module_sources(module: str) -> list[Path]:
    """Source files of a reference module: one ``.py`` file, or a package's code and rules."""
    path = REFERENCE.joinpath(*module.split("."))
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.suffix in {".py", ".yml", ".json"})
    return [path.with_suffix(".py")] if path.with_suffix(".py").is_file() else []


def reference_imports(sources: list[Path]) -> set[str]:
    """``guardrails`` modules imported by the given Python sources."""
    imported: set[str] = set()
    for source in sources:
        if source.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "guardrails."
            ):
                imported.add(node.module or "")
    return imported


class ChildEnvironmentTests(unittest.TestCase):
    def test_repository_commands_never_see_harness_variables(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT": "agent-name", "GIT_DIR": "x", "KEEP": "1"}):
            repo_env = child_env.repo_environment()
            clean = child_env.clean_environment(["KEEP"])
        assert "AGENT" not in repo_env
        assert (repo_env["GIT_DIR"], repo_env["KEEP"]) == ("x", "1")
        assert not {"AGENT", "GIT_DIR", "KEEP"} & set(clean)
        assert "AGENT" not in child_env.repo_environment({"AGENT": "a", "PATH": "/bin"})


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

    def parity(self, options: dict[str, Any], *argv: str) -> list[Any]:
        profile = {"guardrails": {"toolchain_parity": options}}
        return toolchain_parity.run(self.repo, profile, list(argv))

    def test_consistent_uv_toolchain_passes(self) -> None:
        assert self.parity({"uv": self.uv}) == []

    def test_partial_installer_extras_are_an_inventory_finding(self) -> None:
        self.write(".github/workflows/ci.yml", "    - run: uv sync --extra dev --locked\n")
        findings = self.parity({"uv": self.uv})
        assert "PARITY.dependency-inventory" in rules_of(findings)
        assert findings[0].path == ".github/workflows/ci.yml:1"

    def test_installers_for_other_projects_are_not_compared(self) -> None:
        self.write(".github/workflows/ci.yml", "run: uv sync --project tools --extra x --locked\n")
        assert "PARITY.dependency-inventory" not in rules_of(self.parity({"uv": self.uv}))

    def test_documented_installs_must_match_a_maintained_installer(self) -> None:
        self.write("README.md", "```bash\nuv sync --extra dev\nmake install-everything\n```\n")
        findings = self.parity({"uv": self.uv})
        assert rules_of(findings) == ["PARITY.install-guidance"] * 2

    def test_lock_drift_is_a_lock_version_finding(self) -> None:
        self.write("uv.lock", LOCK.replace('version = "8.1.7"', 'version = "9.0.0"'))
        findings = self.parity({"uv": self.uv})
        assert rules_of(findings) == ["PARITY.lock-version"]
        assert "click" in findings[0].message

    def test_uv_pin_must_be_exact_and_locked(self) -> None:
        self.write("pyproject.toml", PYPROJECT.replace("uv==0.9.2", "uv>=0.9"))
        assert "exact uv" in self.parity({"uv": self.uv})[0].message
        self.write("pyproject.toml", PYPROJECT.replace("uv==0.9.2", "uv==0.9.3"))
        assert "PARITY.lock-version" in rules_of(self.parity({"uv": self.uv}))

    def test_python_pins_must_match_each_other_and_the_interpreter(self) -> None:
        running = platform.python_version()
        self.write(".python-version", f"{running}\n")
        self.write("app/.python-version", f"{running}\n")
        options = {"python_pins": [".python-version", "app/.python-version"]}
        assert self.parity(options) == []
        self.write("app/.python-version", "3.0.0\n")
        assert rules_of(self.parity(options)) == ["GATE.local-ci-parity"] * 2
        (self.repo / "app/.python-version").unlink()
        assert "missing" in self.parity(options)[0].message

    def test_prettier_must_be_exactly_locked_and_never_fetched_by_npx(self) -> None:
        version = "3.9.10"
        self.write("package.json", json.dumps({"devDependencies": {"prettier": version}}))
        lock: dict[str, Any] = {
            "packages": {
                "": {"devDependencies": {"prettier": version}},
                "node_modules/prettier": {"version": version, "integrity": "sha", "resolved": "u"},
            }
        }
        self.write("package-lock.json", json.dumps(lock))
        options = {"prettier": {"npx_sources": ["Makefile"]}}
        assert self.parity(options) == []
        self.write("Makefile", "fmt:\n\tnpx prettier --write .\n")
        assert [f.path for f in self.parity(options)] == ["Makefile"]
        assert rules_of(self.parity(options)) == ["PRETTIER.version"]
        self.write("Makefile", "")
        self.write("package.json", json.dumps({"devDependencies": {"prettier": "^3.9.10"}}))
        assert "package.json" in [f.path for f in self.parity(options)]
        self.write("package.json", json.dumps({"devDependencies": {"prettier": version}}))
        lock["packages"]["node_modules/prettier"]["version"] = "3.9.9"
        self.write("package-lock.json", json.dumps(lock))
        assert rules_of(self.parity(options)) == ["PRETTIER.version"]
        lock["packages"]["node_modules/prettier"]["version"] = version
        lock["packages"]["node_modules/other/node_modules/prettier"] = {"version": "3.9.9"}
        self.write("package-lock.json", json.dumps(lock))
        assert rules_of(self.parity(options)) == ["PRETTIER.version"]

    def test_node_pins_must_be_exact_equal_and_match_the_running_node(self) -> None:
        pins = {".nvmrc": b"v22.11.0\n", ".node-version": b"v22.11.0\n"}
        assert toolchain_parity.node_pin_findings(pins, "22.11.0") == []
        findings = toolchain_parity.node_pin_findings({**pins, ".node-version": b"lts/*\n"}, "22.11.0")
        assert rules_of(findings) == ["GATE.local-ci-parity"] * 2
        assert "not one exact X.Y.Z" in findings[1].message
        findings = toolchain_parity.node_pin_findings(pins, "20.1.0")
        assert [f.path for f in findings] == [".nvmrc", ".node-version"]
        assert "running Node 20.1.0" in findings[0].message
        missing = toolchain_parity.node_pin_findings({".nvmrc": None}, "22.11.0")
        assert "pin file is missing" in missing[0].message

    def test_an_unrunnable_node_blocks_instead_of_passing_the_pin(self) -> None:
        findings = toolchain_parity.node_pin_findings({".nvmrc": b"22.11.0\n"}, None)
        assert rules_of(findings) == ["GATE.local-ci-parity"]
        assert "node --version failed" in findings[0].message
        assert toolchain_parity.node_version(self.repo / "missing-directory") is None

    def test_package_manager_pin_selects_the_only_lockfile(self) -> None:
        options = {"package_manager": {"package_json": "package.json"}}
        self.write("package.json", json.dumps({"packageManager": "bun@1.3.0"}))
        self.write("bun.lock", "{}\n")
        assert self.parity(options) == []
        self.write("package-lock.json", "{}\n")
        self.write("bun.lockb", "")
        findings = self.parity(options)
        assert rules_of(findings) == ["PARITY.package-manager"] * 2
        assert [f.path for f in findings] == ["package-lock.json", "bun.lockb"]
        (self.repo / "bun.lock").unlink()
        assert "bun.lock" in [f.path for f in self.parity(options)]
        for declared in ("bun@^1.3", "bun", "deno@2.0.0", None):
            with self.subTest(declared=declared):
                self.write("package.json", json.dumps({"packageManager": declared}))
                findings = self.parity(options)
                assert rules_of(findings) == ["PARITY.package-manager"]
                assert "not one exact" in findings[0].message

    def test_go_toolchain_in_go_mod_matches_every_ci_go_version(self) -> None:
        options = {"go": {"ci_sources": [".github/workflows/*.yml"]}}
        self.write("go.mod", "module example.com/shop\n\ngo 1.24.0\n\ntoolchain go1.24.4\n")
        self.write(".github/workflows/ci.yml", "    - uses: actions/setup-go@v5\n      with:\n        go-version-file: go.mod\n")
        assert self.parity(options) == []
        self.write(".github/workflows/lint.yml", "    container: golang:1.24.4\n    - with:\n        go-version: '1.23.1'\n")
        findings = self.parity(options)
        assert rules_of(findings) == ["GATE.local-ci-parity"]
        assert "CI Go 1.23.1 differs from go.mod 1.24.4" in findings[0].message
        self.write(".github/workflows/lint.yml", "    go-version-file: tools/go.mod\n")
        assert "is not go.mod" in self.parity(options)[0].message
        self.write(".github/workflows/lint.yml", "")
        self.write(".github/workflows/ci.yml", "    - run: go test ./...\n")
        assert "set no Go version" in self.parity(options)[0].message
        self.write("go.mod", "module example.com/shop\n\ngo 1.24\n")
        assert "no exact goX.Y.Z toolchain" in self.parity(options)[0].message

    def test_unconfigured_sections_never_pass_silently(self) -> None:
        assert rules_of(self.parity({})) == ["GATE.local-ci-parity"]

    def test_arguments_are_refused_and_no_audit_mode_remains(self) -> None:
        for argv in (["audit"], ["static"], ["--ci"]):
            with self.subTest(argv=argv):
                findings = self.parity({"uv": self.uv}, *argv)
                assert rules_of(findings) == ["GATE.local-ci-parity"]
                assert "takes no arguments" in findings[0].message

    def test_unreadable_inputs_are_findings(self) -> None:
        (self.repo / "uv.lock").unlink()
        assert rules_of(self.parity({"uv": self.uv})) == ["PARITY.lock-version"]


class VersionMatchingTests(unittest.TestCase):
    def test_pep_440_ordering(self) -> None:
        key = versions.version_key
        ordered = ["1.0.dev1", "1.0a1", "1.0b2", "1.0rc1", "1.0", "1.0.post1.dev1", "1.0.post1"]
        assert sorted(ordered, key=key) == ordered
        assert key("1.0") == key("1.0.0")
        assert key("1!0.1") > key("2.0")

    def test_requirement_parsing_and_specifiers(self) -> None:
        requirement = versions.parse_requirement('Foo_Bar[x] >=1.2, <2 ; python_version > "3"')
        assert requirement.name == "foo-bar"
        assert requirement.admits("1.9.9")
        assert not requirement.admits("2.0")
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
                assert versions.parse_requirement(text).admits(version) == admitted

    def test_unsupported_input_raises(self) -> None:
        for text in ("", "pkg >> 1", "pkg~=1"):
            with self.subTest(text=text), self.assertRaises(versions.VersionError):
                versions.parse_requirement(text).admits("1.0")


if __name__ == "__main__":
    unittest.main()
