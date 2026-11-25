from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from guardrails.host.process import run_text

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/agent-rails/engine")
)


import pytest

from guardrails.commands.catalogue import toolchain_parity

pytestmark = pytest.mark.seam


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    return run_text(["git", "-C", str(repo), *args], check=True, timeout=60, env=env)


def rules_of(findings: list[Any]) -> list[str]:
    return [finding.rule_id for finding in findings]


class RepoCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch = tempfile.TemporaryDirectory(prefix="omp-catalogue-")
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
        self.write(
            ".github/workflows/ci.yml", "    - run: uv sync --all-extras --locked\n"
        )
        self.uv = {
            "project": "pyproject.toml",
            "lock": "uv.lock",
            "installer_sources": ["Makefile", ".github/workflows/*.yml"],
            "guidance": ["README.md"],
            "exact_uv_pin": True,
        }

    def parity(self, options: dict[str, Any], *argv: str) -> list[Any]:
        profile = {"checkers": {"toolchain_parity": options}}
        return toolchain_parity.run(self.repo, profile, list(argv))

    def test_consistent_uv_toolchain_passes(self) -> None:
        assert self.parity({"uv": self.uv}) == []

    def test_partial_installer_extras_are_an_inventory_finding(self) -> None:
        self.write(
            ".github/workflows/ci.yml", "    - run: uv sync --extra dev --locked\n"
        )
        findings = self.parity({"uv": self.uv})
        assert "PARITY.dependency-inventory" in rules_of(findings)
        assert findings[0].path == ".github/workflows/ci.yml:1"

    def test_installers_for_other_projects_are_not_compared(self) -> None:
        self.write(
            ".github/workflows/ci.yml",
            "run: uv sync --project tools --extra x --locked\n",
        )
        assert "PARITY.dependency-inventory" not in rules_of(
            self.parity({"uv": self.uv})
        )

    def test_documented_installs_must_match_a_maintained_installer(self) -> None:
        self.write(
            "README.md", "```bash\nuv sync --extra dev\nmake install-everything\n```\n"
        )
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
        self.write(
            "package.json", json.dumps({"devDependencies": {"prettier": version}})
        )
        lock: dict[str, Any] = {
            "packages": {
                "": {"devDependencies": {"prettier": version}},
                "node_modules/prettier": {
                    "version": version,
                    "integrity": "sha",
                    "resolved": "u",
                },
            }
        }
        self.write("package-lock.json", json.dumps(lock))
        options = {"prettier": {"npx_sources": ["Makefile"]}}
        assert self.parity(options) == []
        self.write("Makefile", "fmt:\n\tnpx prettier --write .\n")
        assert [f.path for f in self.parity(options)] == ["Makefile"]
        assert rules_of(self.parity(options)) == ["PRETTIER.version"]
        self.write("Makefile", "")
        self.write(
            "package.json", json.dumps({"devDependencies": {"prettier": "^3.9.10"}})
        )
        assert "package.json" in [f.path for f in self.parity(options)]
        self.write(
            "package.json", json.dumps({"devDependencies": {"prettier": version}})
        )
        lock["packages"]["node_modules/prettier"]["version"] = "3.9.9"
        self.write("package-lock.json", json.dumps(lock))
        assert rules_of(self.parity(options)) == ["PRETTIER.version"]
        lock["packages"]["node_modules/prettier"]["version"] = version
        lock["packages"]["node_modules/other/node_modules/prettier"] = {
            "version": "3.9.9"
        }
        self.write("package-lock.json", json.dumps(lock))
        assert rules_of(self.parity(options)) == ["PRETTIER.version"]

    def test_node_pins_must_be_exact_equal_and_match_the_running_node(self) -> None:
        pins = {".nvmrc": b"v22.11.0\n", ".node-version": b"v22.11.0\n"}
        assert toolchain_parity.node_pin_findings(pins, "22.11.0") == []
        findings = toolchain_parity.node_pin_findings(
            {**pins, ".node-version": b"lts/*\n"}, "22.11.0"
        )
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
        self.write(
            "go.mod", "module example.com/shop\n\ngo 1.24.0\n\ntoolchain go1.24.4\n"
        )
        self.write(
            ".github/workflows/ci.yml",
            "    - uses: actions/setup-go@v5\n      with:\n        go-version-file: go.mod\n",
        )
        assert self.parity(options) == []
        self.write(
            ".github/workflows/lint.yml",
            "    container: golang:1.24.4\n    - with:\n        go-version: '1.23.1'\n",
        )
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


if __name__ == "__main__":
    unittest.main()
