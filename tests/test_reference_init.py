"""omp-guardrails init: greenfield, adopt and extend modes, idempotency, hook, pin and LSP."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "agent/skills/setup-repository-guardrails/assets/python-reference"
SKILL_TEMPLATE = ROOT / "agent/skills/test-guidance/assets/templates/test-profile.md"
sys.path.insert(0, str(REFERENCE))

from omp_guardrails import cli  # noqa: E402
from omp_guardrails.config.house_style import load_house_style  # noqa: E402
from omp_guardrails.config.profile import load_profile  # noqa: E402
from omp_guardrails.init import gate_config, lsp, pin  # noqa: E402

COMMIT = "0123456789abcdef0123456789abcdef01234567"
PIN_ARGS = ["--pin-url", "https://example.invalid/reference.git", "--pin-commit", COMMIT]
SERVER_PRESENT = "/usr/local/bin/language-server"


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


def snapshot(repo: Path) -> dict[str, object]:
    """Every file's bytes and mode outside .git, plus the hook path setting."""
    files = {
        str(path.relative_to(repo)): (path.read_bytes(), path.stat().st_mode)
        for path in sorted(repo.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(repo).parts[:1]
    }
    return {"files": files, "hooksPath": git(repo, "config", "--get", "core.hooksPath")}


class InitCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="omp-init-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name).resolve() / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        (self.repo / "pyproject.toml").write_text('[project]\nname = "target"\n')
        which = mock.patch.object(lsp.shutil, "which", return_value=SERVER_PRESENT)
        which.start()
        self.addCleanup(which.stop)

    def init(self, *args: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(["init", "--repo", str(self.repo), *args])
        return code, output.getvalue()

    def write(self, relative: str, text: str) -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def gate(self) -> dict:
        return json.loads((self.repo / ".omp/gate.json").read_text())

    def assert_second_run_changes_nothing(self, *args: str) -> None:
        before = snapshot(self.repo)
        code, output = self.init(*args)
        self.assertEqual(code, 0, output)
        self.assertIn("no changes", output)
        self.assertEqual(snapshot(self.repo), before)


class GreenfieldTests(InitCase):
    def test_writes_profile_gate_hook_and_pin(self) -> None:
        code, output = self.init("--ci-host", "github", *PIN_ARGS)
        self.assertEqual(code, 0, output)
        self.assertIn("init (greenfield)", output)
        self.assertIn(
            f"omp-guardrails @ git+https://example.invalid/reference.git@{COMMIT}"
            "#subdirectory=agent/skills/setup-repository-guardrails/assets/python-reference",
            output,
        )
        self.assertEqual(
            self.gate(),
            {
                "schemaVersion": 2,
                "checks": {
                    "commit": ["omp-guardrails", "profile-check"],
                    "ci": ["omp-guardrails", "profile-check"],
                },
                "pathCheck": ["omp-guardrails", "path-check"],
                "ciHost": "github",
            },
        )
        profile = load_profile(self.repo)
        self.assertEqual(profile["house_style"]["version"], load_house_style()["version"])
        self.assertEqual(
            profile["command_notes"]["lsp"],
            [{"stack": "python", "server": "pyright-langserver", "install": lsp.STACKS[0].install}],
        )
        self.assertEqual(profile["tiers"]["t1"]["selector"], "<selector>")
        hook_file = self.repo / ".githooks/pre-commit"
        self.assertTrue(os.access(hook_file, os.X_OK))
        self.assertEqual(git(self.repo, "config", "--get", "core.hooksPath"), ".githooks")

    def test_second_run_changes_nothing(self) -> None:
        self.assertEqual(self.init("--ci-host", "github", *PIN_ARGS)[0], 0)
        self.assert_second_run_changes_nothing("--ci-host", "github", *PIN_ARGS)
        self.assert_second_run_changes_nothing("--greenfield", *PIN_ARGS)

    def test_creates_no_empty_folder(self) -> None:
        self.assertEqual(self.init(*PIN_ARGS)[0], 0)
        for path in self.repo.rglob("*"):
            if path.is_dir() and ".git" not in path.relative_to(self.repo).parts[:1]:
                self.assertTrue(any(path.iterdir()), f"{path} is empty")

    def test_installed_hook_runs_checks_commit_and_propagates_failure(self) -> None:
        self.assertEqual(self.init(*PIN_ARGS)[0], 0)
        hook_file = self.repo / ".githooks/pre-commit"
        for status in (0, 3):
            gate = self.gate()
            gate["checks"]["commit"] = [sys.executable, "-c", f"raise SystemExit({status})"]
            self.write(".omp/gate.json", json.dumps(gate))
            result = subprocess.run(
                [str(hook_file)], cwd=self.repo, env=clean_environment(), check=False
            )
            self.assertEqual(result.returncode, status)

    def test_multiple_checks_all_run_in_one_argv(self) -> None:
        log = self.repo.parent / "ran.log"
        recorder = self.repo.parent / "record.py"
        recorder.write_text(
            "import sys\n"
            f"open({str(log)!r}, 'a').write(sys.argv[1] + '\\n')\n"
            "raise SystemExit(4 if sys.argv[1] == 'naming' else 0)\n"
        )
        reference = shlex.join([sys.executable, str(recorder)])
        args = ("--reference-command", reference, "--check", "naming", "--check", "structure")
        self.assertEqual(self.init(*args, *PIN_ARGS)[0], 0)
        result = subprocess.run(self.gate()["checks"]["commit"], cwd=self.repo, check=False)
        self.assertEqual(result.returncode, 4)
        self.assertEqual(log.read_text().split(), ["naming", "structure"])

    def test_keeps_an_installed_hook(self) -> None:
        installed = self.write(".git/hooks/pre-commit", "#!/bin/sh\nexit 0\n")
        installed.chmod(0o755)
        code, output = self.init(*PIN_ARGS)
        self.assertEqual(code, 0, output)
        self.assertIn("kept the installed pre-commit hook", output)
        self.assertFalse((self.repo / ".githooks").exists())
        self.assertEqual(installed.read_text(), "#!/bin/sh\nexit 0\n")

    def test_hook_manager_without_installed_hook_is_a_gap(self) -> None:
        self.write(".pre-commit-config.yaml", "repos: []\n")
        code, output = self.init(*PIN_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("GATE.pre-commit", output)
        self.assertFalse((self.repo / ".githooks").exists())

    def test_rejects_unknown_or_recursive_checks(self) -> None:
        for name in ("init", "gate", "no-such-check"):
            code, output = self.init("--check", name, *PIN_ARGS)
            self.assertEqual(code, 1)
            self.assertIn("--check names no gate subcommand", output)
        self.assertFalse((self.repo / ".omp").exists())


class AdoptTests(InitCase):
    def setUp(self) -> None:
        super().setUp()
        self.existing = {
            "schemaVersion": 2,
            "checks": {"commit": ["make", "gate-commit"], "ci": ["make", "gate-ci"]},
            "pathCheck": ["python3", "scripts/check-paths.py"],
            "ciHost": "github",
            "agentMerge": "allowed",
        }
        self.write(".omp/gate.json", json.dumps(self.existing))
        profile = json.dumps(
            {
                "adapter": "pytest",
                "verification_surface": {"none": "library without a UI", "approval": "ADR-7"},
                "guardrails": {"naming": {"roots": ["src"]}},
            }
        )
        self.write(".omp/test-profile.md", f"---\n{profile}\n---\n\n# Our profile\n")
        self.settings = self.repo.parent / "settings.json"
        self.settings.write_text(
            json.dumps({"naming": {"roots": ["lib"], "extra": 1}, "paths": {"allow": ["src/"]}})
        )

    def adopt(self, *extra: str) -> tuple[int, str]:
        return self.init("--adopt", "--settings", str(self.settings), *PIN_ARGS, *extra)

    def test_keeps_existing_checks_and_adds_reference_after_them(self) -> None:
        code, output = self.adopt("--ci-host", "other-host")
        self.assertEqual(code, 0, output)
        gate = self.gate()
        for event, existing in (("commit", ["make", "gate-commit"]), ("ci", ["make", "gate-ci"])):
            argv = gate["checks"][event]
            self.assertEqual(argv[:2], ["sh", "-c"])
            self.assertEqual(argv[3:], [gate_config.SHADOW_NAME, *existing])
            self.assertIn("omp-guardrails profile-check", argv[2])
        self.assertEqual(gate["pathCheck"], self.existing["pathCheck"])
        self.assertEqual(gate["ciHost"], "github")
        self.assertEqual(gate["agentMerge"], "allowed")
        self.assertIn("keeps ciHost 'github'", output)

    def test_shadow_argv_runs_both_and_fails_when_either_fails(self) -> None:
        log = self.repo.parent / "ran.log"
        script = f"import sys; open({str(log)!r}, 'a').write(sys.argv[1] + '\\n')"
        argv = gate_config.shadow_argv(
            [sys.executable, "-c", "raise SystemExit(5)"],
            [sys.executable, "-c", script],
            ["profile-check"],
        )
        result = subprocess.run(argv, cwd=self.repo, check=False)
        self.assertEqual(result.returncode, 5)
        self.assertEqual(log.read_text().split(), ["profile-check"])

    def test_profile_keeps_values_and_fills_gaps_and_settings(self) -> None:
        self.assertEqual(self.adopt()[0], 0)
        profile = load_profile(self.repo)
        self.assertEqual(profile["adapter"], "pytest")
        self.assertEqual(
            profile["verification_surface"],
            {"none": "library without a UI", "approval": "ADR-7"},
        )
        self.assertEqual(profile["guardrails"]["naming"], {"roots": ["src"], "extra": 1})
        self.assertEqual(profile["guardrails"]["paths"], {"allow": ["src/"]})
        self.assertIn("tiers", profile)
        text = (self.repo / ".omp/test-profile.md").read_text()
        self.assertTrue(text.endswith("# Our profile\n"))

    def test_second_run_changes_nothing(self) -> None:
        self.assertEqual(self.adopt()[0], 0)
        self.assert_second_run_changes_nothing(
            "--adopt", "--settings", str(self.settings), *PIN_ARGS
        )
        self.assert_second_run_changes_nothing(*PIN_ARGS)

    def test_existing_gate_without_mode_needs_a_choice(self) -> None:
        before = snapshot(self.repo)
        code, output = self.init(*PIN_ARGS)
        self.assertEqual(code, 1)
        self.assertIn("choose --adopt or --extend", output)
        self.assertEqual(snapshot(self.repo), before)

    def test_schema_v1_gate_is_reported_not_rewritten(self) -> None:
        self.write(".omp/gate.json", json.dumps({"schemaVersion": 1}))
        code, output = self.adopt()
        self.assertEqual(code, 1)
        self.assertIn("GATE.local-ci-parity", output)
        self.assertEqual(self.gate(), {"schemaVersion": 1})


class ExtendTests(InitCase):
    def test_adds_missing_sections_and_touches_nothing_else(self) -> None:
        values = {"adapter": "pytest", "command_notes": {"lsp": [{"server": "custom"}]}}
        self.write(".omp/test-profile.md", f"---\n{json.dumps(values)}\n---\nbody\n")
        self.write(".omp/gate.json", json.dumps({"schemaVersion": 2, "checks": {"commit": ["x"]}}))
        gate_before = (self.repo / ".omp/gate.json").read_text()
        code, output = self.init("--extend")
        self.assertEqual(code, 0, output)
        profile = load_profile(self.repo)
        self.assertEqual(profile["adapter"], "pytest")
        self.assertEqual(profile["command_notes"]["lsp"], [{"server": "custom"}])
        self.assertEqual(
            set(profile["verification_surface"]),
            {
                "lever_command",
                "doctor_marker",
                "feature_map_path",
                "journey_file_pattern",
                "data_dir_arg",
                "evidence_dir_arg",
            },
        )
        self.assertEqual((self.repo / ".omp/gate.json").read_text(), gate_before)
        self.assertFalse((self.repo / ".githooks").exists())
        self.assertEqual(git(self.repo, "config", "--get", "core.hooksPath"), "")
        self.assertNotIn("pin:", output)
        self.assert_second_run_changes_nothing("--extend")

    def test_needs_an_existing_profile(self) -> None:
        code, output = self.init("--extend")
        self.assertEqual(code, 1)
        self.assertIn("--extend needs an existing", output)
        self.assertFalse((self.repo / ".omp").exists())


class LspTests(InitCase):
    def test_missing_server_is_a_setup_gap_unless_allowed(self) -> None:
        with mock.patch.object(lsp.shutil, "which", return_value=None):
            code, output = self.init(*PIN_ARGS)
            self.assertEqual(code, 1)
            self.assertIn("PROFILE.complete", output)
            self.assertIn("pyright-langserver is not on PATH", output)
            code, output = self.init(*PIN_ARGS, "--allow-missing-lsp")
        self.assertEqual(code, 0, output)
        self.assertIn("allowed by --allow-missing-lsp", output)

    def test_detects_typescript_markers(self) -> None:
        (self.repo / "pyproject.toml").unlink()
        self.write("tsconfig.json", "{}")
        stacks = lsp.detect(self.repo)
        self.assertEqual([stack.server for stack in stacks], ["typescript-language-server"])

    def test_repo_without_stack_records_no_server(self) -> None:
        (self.repo / "pyproject.toml").unlink()
        code, output = self.init(*PIN_ARGS)
        self.assertEqual(code, 0, output)
        self.assertNotIn("lsp", load_profile(self.repo)["command_notes"])
        self.assertIn("lsp: no pyproject.toml", output)


class PinTests(unittest.TestCase):
    def test_reads_a_git_install_record(self) -> None:
        record = json.dumps(
            {
                "url": "https://example.invalid/reference.git",
                "vcs_info": {"vcs": "git", "commit_id": COMMIT},
                "subdirectory": "pkg",
            }
        )
        source = pin.resolve(pin.PinSource(), pin.parse_direct_url(record))
        line, findings = pin.pin_line(source)
        self.assertEqual(findings, [])
        self.assertEqual(
            line,
            f"omp-guardrails @ git+https://example.invalid/reference.git@{COMMIT}#subdirectory=pkg",
        )

    def test_unknown_source_is_a_gap(self) -> None:
        source = pin.resolve(pin.PinSource(commit="main"), pin.parse_direct_url('{"dir_info": {}}'))
        line, findings = pin.pin_line(source)
        self.assertIsNone(line)
        self.assertEqual({finding.rule_id for finding in findings}, {"PARITY.lock-version"})
        self.assertEqual(len(findings), 2)


class TemplateTests(unittest.TestCase):
    def test_packaged_template_matches_the_test_guidance_template(self) -> None:
        packaged = REFERENCE / "omp_guardrails/init/test-profile.md"
        self.assertEqual(packaged.read_bytes(), SKILL_TEMPLATE.read_bytes())


if __name__ == "__main__":
    unittest.main()
