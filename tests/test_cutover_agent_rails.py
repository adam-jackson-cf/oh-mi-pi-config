"""scripts/cutover_agent_rails.py records: the one-off conversion of repo records into config.json.

Synthetic records under tests/fixtures/agentrails/records copy the profile template's sections and
the gate shapes of the four live records. git is replaced by a fake clone inspector here; the seam
tests drive real checkouts.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.fast

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "tests/fixtures/agentrails/records"
spec = importlib.util.spec_from_file_location(
    "cutover_agent_rails", ROOT / "scripts/cutover_agent_rails.py"
)
assert spec is not None
assert spec.loader is not None
cutover = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = cutover
spec.loader.exec_module(cutover)

from guardrails.commands import cli  # noqa: E402 - the script put the engine on sys.path

OWNER = "example-owner"
NAMES = ("allowed-app", "gated-tool", "plain-lib")
BY = "example-owner"
AT = "2026-11-24T09:00:00Z"


class Workspace(unittest.TestCase):
    """A copied records root and one fake main checkout per record, under a temp directory."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp)
        self.root = self.temp / "repos"
        shutil.copytree(RECORDS, self.root)
        self.clones = {}
        for name in NAMES:
            clone = self.temp / "clones" / name
            (clone / ".git" / "info").mkdir(parents=True)
            self.clones[name] = clone
        self.origins = {name: f"{OWNER}/{name}" for name in NAMES}
        inspector = patch.object(cutover, "inspect_clone", self.inspect)
        inspector.start()
        self.addCleanup(inspector.stop)

    def inspect(self, clone: Path) -> Any:
        return cutover.Clone(clone, self.origins[clone.name], "main")

    def args(
        self, *names: str, attest: bool = True, extra: tuple[str, ...] = ()
    ) -> list[str]:
        argv = ["records", "--records-root", str(self.root)]
        for name in names or NAMES:
            argv += ["--repo", f"{OWNER}/{name}={self.clones[name]}"]
        if attest:
            argv += ["--approved-by", BY, "--approved-at", AT]
        return argv + list(extra)

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cutover.main(argv)
        return code, out.getvalue(), err.getvalue()

    def config(self, name: str) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(
            (self.clones[name] / ".agentrails/config.json").read_text()
        )
        return loaded

    def snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.temp)): path.read_bytes()
            for path in sorted(self.temp.rglob("*"))
            if path.is_file()
        }

    def apply(self, *names: str, extra: tuple[str, ...] = ()) -> tuple[int, str, str]:
        archive = ("--archive", str(self.temp / "archive"), "--apply")
        return self.run_main(self.args(*names, extra=(*archive, *extra)))


class DryRun(Workspace):
    def test_prints_every_config_and_its_dropped_fields_and_writes_nothing(
        self,
    ) -> None:
        before = self.snapshot()
        code, out, err = self.run_main(self.args())
        assert (code, err) == (0, "")
        assert self.snapshot() == before
        for name in NAMES:
            assert (
                f"== {OWNER}/{name} -> {self.clones[name]}/.agentrails/config.json"
                in out
            )
        assert "gate.json checks.profile (profile.md no longer exists)" in out
        assert "guardrails.check.run step 'profile-check' (retired)" in out
        assert "guardrail-proposal.json (archived)" in out
        assert "profile.md prose after the front matter" in out
        assert out.rstrip().endswith("dry run: nothing written")

    def test_reports_the_records_it_would_delete(self) -> None:
        before = self.snapshot()
        code, out, _ = self.run_main(
            self.args("plain-lib", extra=("--delete", f"{OWNER}/draft"))
        )
        assert code == 0
        assert f"{OWNER}/draft: would delete {self.root / OWNER / 'draft'}" in out
        assert self.snapshot() == before


class Attestation(Workspace):
    def test_missing_attestation_exits_2_naming_the_counts(self) -> None:
        before = self.snapshot()
        code, out, err = self.run_main(self.args(attest=False))
        assert (code, out) == (2, "")
        assert "owner attestation missing" in err
        assert f"{OWNER}/allowed-app: 2 exceptions and 5 approvals need" in err
        assert f"{OWNER}/gated-tool: 0 exceptions and 6 approvals need" in err
        assert f"{OWNER}/plain-lib: 0 exceptions and 1 approvals need" in err
        assert self.snapshot() == before

    def test_half_an_attestation_is_missing(self) -> None:
        code, _, err = self.run_main([
            *self.args("plain-lib", attest=False),
            "--approved-by",
            BY,
        ])
        assert code == 2
        assert "need --approved-by and --approved-at" in err

    def test_malformed_attestation_exits_2(self) -> None:
        for flags, message in (
            (("--approved-by", " "), "--approved-by must be"),
            (("--approved-at", "2026-11-24"), "--approved-at must be"),
        ):
            code, _, err = self.run_main([
                *self.args("plain-lib", attest=False),
                *flags,
            ])
            assert code == 2
            assert message in err


class Conversion(Workspace):
    def setUp(self) -> None:
        super().setUp()
        code, _, err = self.apply()
        assert code == 0, err

    def test_every_config_passes_config_check(self) -> None:
        for name in NAMES:
            path = self.clones[name] / ".agentrails/config.json"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = cli.main([
                    "config-check",
                    "--file",
                    str(path),
                    "--slug",
                    f"{OWNER}/{name}",
                ])
            assert (code, json.loads(out.getvalue())["problems"]) == (0, [])
            assert path.stat().st_mode & 0o777 == 0o644

    def test_gate_maps_to_autonomy_ci_gates_and_commands(self) -> None:
        allowed, gated, plain = (self.config(name) for name in NAMES)
        assert allowed["autonomy"] == dict.fromkeys(
            ("branch", "pr", "merge"), "allowed"
        )
        assert gated["autonomy"] == allowed["autonomy"]
        assert plain["autonomy"] == dict.fromkeys(("branch", "pr", "merge"), "ask")
        assert allowed["ci"] == {
            "enabled": True,
            "requiredContext": "quality-gates",
            "runner": "github-actions",
        }
        assert gated["ci"]["runner"] == "ci-gate"
        assert plain["ci"] == {"enabled": False}
        assert (allowed["gates"]["audit"], gated["gates"]["audit"]) == (False, True)
        assert "profile" not in allowed["commands"]
        assert allowed["commands"]["format"] == ["uv", "run", "ruff", "format"]
        assert plain["mergePolicies"] == []
        assert gated["mergePolicies"][0]["base"] == "nuc-config"
        assert gated["afterMerge"] == {
            "argv": ["bash", "scripts/sync.sh"],
            "paths": ["agent/**"],
        }
        assert "afterMerge" not in plain
        assert allowed["judgments"] == {}
        assert allowed["gates"]["rules"] == {}

    def test_repo_and_onboarding_name_the_migration(self) -> None:
        config = self.config("allowed-app")
        assert config["schemaVersion"] == 1
        assert config["repo"] == {
            "slug": f"{OWNER}/allowed-app",
            "defaultBranch": "main",
        }
        onboarding = config["onboarding"]
        assert onboarding["origin"] == "migration"
        assert onboarding["migratedFrom"] == f"~/.omp/repos/{OWNER}/allowed-app"
        assert onboarding["skills"] == {}
        assert list(config) == [
            name for name in cutover.repo_config.SECTIONS if name in config
        ]

    def test_exemptions_keep_both_texts_in_their_approval(self) -> None:
        gated = self.config("gated-tool")
        assert gated["tests"]["tiers"]["t3"] == {"none": True}
        assert gated["tests"]["verification_surface"] == {"none": True}
        assert gated["tests"]["run_events"]["nightly_scheduler"] == {"none": True}
        reasons = {
            entry["target"]: entry["reason"]
            for entry in gated["approvals"]
            if entry["kind"] == "exemption"
        }
        assert reasons["tiers.t3"] == (
            "no T3 suites: the repository has no exploratory or scheduled tests "
            "(owner chat 2026-11-19: approved T3 exemption (D1))"
        )
        assert set(reasons) == {
            "tiers.t3",
            "verification_surface",
            "run_events.nightly_scheduler",
        }
        allowed = self.config("allowed-app")
        assert allowed["tests"]["identity_artefacts"] == {"none": True}

    def test_approvals_move_out_of_machines_and_checkers_and_are_stamped(self) -> None:
        allowed = self.config("allowed-app")
        by_kind = {
            (entry["kind"], entry["target"]): entry for entry in allowed["approvals"]
        }
        assert by_kind["budget", "m-efd8d651c839"]["reason"].startswith(
            "owner approval 2026-11-16"
        )
        assert by_kind["ratchet", "src/app/detector.py"]["reason"].startswith(
            "Owner approval"
        )
        assert by_kind["skip", "tests/test_slow.py::test_container_*"]["reason"] == (
            "needs a container runtime (owner chat 2026-11-20)"
        )
        assert all(
            (entry["approvedBy"], entry["approvedAt"]) == (BY, AT)
            for entry in [*allowed["approvals"], *allowed["exceptions"]]
        )
        machine = allowed["tests"]["fast_tier_budget"]["machines"][0]
        assert "approval" not in machine
        assert "approvals" not in allowed["checkers"]["coverage_ratchet"]
        assert "approved_skips" not in allowed["checkers"]["test_evidence"]
        assert allowed["exceptions"][0] == {
            "rule": "NAME.conventions",
            "path": "docs/adr/**",
            "check": "child-repeats-parent",
            "reason": "ADR file names follow the ADR numbering convention",
            "approvedBy": BY,
            "approvedAt": AT,
        }

    def test_profile_check_leaves_check_run(self) -> None:
        assert self.config("allowed-app")["checkers"]["check"]["run"] == [
            "surface-lint",
            "adapter lint",
        ]
        assert (
            "profile-check" not in self.config("gated-tool")["checkers"]["check"]["run"]
        )

    def test_structure_gains_ownership_areas_and_house_style_moves(self) -> None:
        config = self.config("allowed-app")
        assert "ownership_areas" in config["structure"]
        assert config["houseStyle"] == {"version": 1}


class Apply(Workspace):
    def test_archives_every_record_file_and_keeps_the_records_root(self) -> None:
        before = {path for path in self.snapshot() if path.startswith("repos/")}
        code, out, _ = self.apply()
        assert code == 0
        [tarball] = (self.temp / "archive").glob("repos-*.tar.gz")
        with tarfile.open(tarball) as tar:
            archived = {member.name for member in tar.getmembers() if member.isfile()}
        assert archived == before
        assert f"repos/{OWNER}/allowed-app/guardrail-proposal.json" in archived
        assert {path for path in self.snapshot() if path.startswith("repos/")} == before
        assert f"archived {self.root}" in out

    def test_writes_the_exclude_line_once(self) -> None:
        (self.clones["gated-tool"] / ".git/info/exclude").write_text(
            "*.log\n/.agentrails/\n"
        )
        (self.clones["plain-lib"] / ".git/info/exclude").write_text("*.log")
        code, out, _ = self.apply()
        assert code == 0
        for name, expected in (
            ("allowed-app", "/.agentrails/\n"),
            ("gated-tool", "*.log\n/.agentrails/\n"),
            ("plain-lib", "*.log\n/.agentrails/\n"),
        ):
            assert (self.clones[name] / ".git/info/exclude").read_text() == expected
        assert "already had /.agentrails/" in out
        assert not cutover.ensure_excluded(
            self.clones["allowed-app"] / ".git/info/exclude"
        )

    def test_an_existing_config_refuses_the_whole_run(self) -> None:
        existing = self.clones["plain-lib"] / ".agentrails/config.json"
        existing.parent.mkdir()
        existing.write_text("{}\n")
        before = self.snapshot()
        code, _, err = self.apply()
        assert code == 1
        assert f"{existing} already exists; the cutover never overwrites it" in err
        assert self.snapshot() == before
        assert not (self.temp / "archive").exists()

    def test_write_config_never_replaces_a_file(self) -> None:
        path = self.temp / "config.json"
        path.write_text("{}\n")
        with pytest.raises(cutover.CutoverError, match="already exists"):
            cutover.write_config(path, {"schemaVersion": 1})
        assert path.read_text() == "{}\n"
        assert [
            entry.name
            for entry in self.temp.iterdir()
            if entry.name.startswith(".config-")
        ] == []

    def test_deletes_named_records_without_archiving_them(self) -> None:
        code, out, _ = self.apply("plain-lib", extra=("--delete", f"{OWNER}/draft"))
        assert code == 0
        assert not (self.root / OWNER / "draft").exists()
        assert (self.root / OWNER / "plain-lib").is_dir()
        [tarball] = (self.temp / "archive").glob("repos-*.tar.gz")
        with tarfile.open(tarball) as tar:
            names = tar.getnames()
        assert not [name for name in names if "/draft/" in name]
        assert f"{OWNER}/draft: deleted" in out

    def test_deleting_the_last_record_of_an_owner_removes_the_owner_directory(
        self,
    ) -> None:
        lone = self.root / "lone-owner" / "draft"
        shutil.copytree(self.root / OWNER / "draft", lone)
        cutover.delete_record(self.root, "lone-owner/draft")
        assert not (self.root / "lone-owner").exists()

    def test_apply_needs_an_archive(self) -> None:
        code, _, err = self.run_main(self.args(extra=("--apply",)))
        assert code == 2
        assert "--apply needs --archive DIR" in err


class Refusals(Workspace):
    def test_origin_mismatch_refuses_the_whole_run(self) -> None:
        self.origins["gated-tool"] = "someone-else/gated-tool"
        before = self.snapshot()
        code, _, err = self.apply()
        assert code == 1
        assert (
            f"{OWNER}/gated-tool: the clone's origin remote names "
            "'someone-else/gated-tool', not the record slug"
        ) in err
        assert self.snapshot() == before

    def test_a_validation_problem_in_one_repo_writes_nothing_anywhere(self) -> None:
        gate = self.root / OWNER / "plain-lib/gate.json"
        gate.write_text(json.dumps({"schemaVersion": 2, "checks": {"commit": []}}))
        before = self.snapshot()
        code, _, err = self.apply()
        assert code == 1
        assert (
            f"{OWNER}/plain-lib: commands.commit: must be a non-empty argv array" in err
        )
        assert self.snapshot() == before

    def test_slugs_need_a_record_and_may_appear_once(self) -> None:
        code, _, err = self.run_main([
            *self.args("plain-lib"),
            "--repo",
            f"{OWNER}/plain-lib={self.temp}",
            "--delete",
            "nobody/none",
        ])
        assert code == 1
        assert f"{OWNER}/plain-lib is passed more than once" in err
        assert f"nobody/none: no record at {self.root / 'nobody/none'}" in err

    def test_repo_arguments_are_checked(self) -> None:
        for text in (
            "example-owner/app",
            "not a slug=/abs",
            "example-owner/app=relative/path",
        ):
            with pytest.raises(cutover.argparse.ArgumentTypeError):
                cutover.parse_target(text)
        assert cutover.parse_target("Example-Owner/App=/abs") == cutover.Target(
            "example-owner/app", Path("/abs")
        )
        with pytest.raises(cutover.argparse.ArgumentTypeError):
            cutover.parse_slug("no-slash")

    def test_a_clone_that_cannot_be_inspected_is_a_problem(self) -> None:
        def broken(clone: Path) -> Any:
            raise cutover.CutoverError(f"{clone} is not in a git repository")

        with patch.object(cutover, "inspect_clone", broken):
            code, _, err = self.run_main(self.args("plain-lib"))
        assert code == 1
        assert "is not in a git repository" in err


def draft(gate: dict[str, Any], profile: dict[str, Any]) -> Any:
    return cutover.convert(gate, profile)


GATE = {"schemaVersion": 2, "checks": {"commit": ["make"]}}


class Records(unittest.TestCase):
    def test_gate_shapes_without_a_mapping_are_problems(self) -> None:
        found = draft(
            {"schemaVersion": 1, "checks": {}, "agentMerge": "sometimes", "mode": "x"},
            {},
        )
        assert found.problems == [
            "gate.json mode has no config mapping",
            "gate.json schemaVersion must be 2",
            "gate.json agentMerge 'sometimes' is not allowed or disabled",
        ]

    def test_profile_shapes_without_a_mapping_are_problems(self) -> None:
        profile = {
            "notes": "x",
            "verification_surface": {"none": "no app"},
            "tiers": {"t3": {"none": "x", "approval": "y", "extra": 1}},
            "guardrails": {
                "exceptions": {},
                "coverage_ratchet": {"approvals": {}},
            },
        }
        found = draft(GATE, profile)
        assert found.problems == [
            "profile.md notes has no config mapping",
            "profile.md tiers.t3 must be {none: <reason>, approval: <reference>}",
            "profile.md verification_surface must be {none: <reason>, approval: <reference>}",
            "profile.md guardrails.exceptions must be a list",
            "profile.md guardrails approvals must be a list",
        ]
        assert draft(GATE, {"guardrails": []}).problems == [
            "profile.md guardrails must be an object"
        ]

    def test_a_lone_profile_check_step_leaves_the_default_steps(self) -> None:
        found = draft(GATE, {"guardrails": {"check": {"run": ["profile-check"]}}})
        assert found.sections["checkers"] == {"check": {}}

    def test_front_matter_parser_refuses_malformed_profiles(self) -> None:
        temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp)
        cases = {
            "# no front matter\n": "must start with a '---' front-matter line",
            "---\n{}\n": "has no closing '---' line",
            "---\n[1]\n---\n": "must hold one JSON object",
            '---\n{"a": 1, "a": 2}\n---\n': "duplicate key 'a'",
        }
        for text, message in cases.items():
            path = temp / "profile.md"
            path.write_text(text)
            with pytest.raises(cutover.CutoverError, match=message):
                cutover.read_front_matter(path)
        path.write_text('---\n{"a": 1}\n---\n\n# Prose\n')
        assert cutover.read_front_matter(path) == ({"a": 1}, "# Prose")


class Rewrites(Workspace):
    def setUp(self) -> None:
        super().setUp()
        rewrites = patch.dict(
            cutover.PATH_REWRITES, {f"{OWNER}/gated-tool": cutover.AGENT_RAILS_REWRITE}
        )
        rewrites.start()
        self.addCleanup(rewrites.stop)

    def test_renames_paths_and_drops_deleted_ones(self) -> None:
        code, out, _ = self.run_main(self.args("gated-tool"))
        assert code == 0
        assert "guardrails check --repo" in out
        assert "tests/repo-record.test.ts (the agent-rails branch deletes it)" in out
        code, _, err = self.apply("gated-tool")
        assert code == 0, err
        config = self.config("gated-tool")
        text = json.dumps(config)
        assert "agent/extensions/dynaflows" not in text
        assert "repo-record" not in text
        assert config["structure"]["owners"]["patterns"] == {
            "agent/extensions/agent-rails/**": "dynaflows",
            "agent/extensions/agent-rails/skills/jev-actions/**": "jev-actions",
        }
        tests = config["tests"]
        assert tests["adapters"][0]["tiers"]["t1"]["location"] == [
            "tests/agent-rails-extension.test.ts"
        ]
        assert tests["fast_tier_budget"]["duration_report_command"] == (
            "bun test ./tests/agent-rails-extension.test.ts"
        )
        assert tests["public_entry_points"] == {
            "guardrails": "guardrails.commands.cli:main"
        }
        assert tests["identity_artefacts"] == {
            "agent/extensions/agent-rails/judgments/**": (
                "calibrated judgment data; change only through a recalibration"
            )
        }

    def test_a_deleted_path_left_in_a_value_is_a_problem(self) -> None:
        profile_path = self.root / OWNER / "gated-tool/profile.md"
        profile, prose = cutover.read_front_matter(profile_path)
        profile["guards"]["process"] = "tests/repo-record.test.ts"
        profile_path.write_text(
            "---\n" + json.dumps(profile) + "\n---\n\n" + prose + "\n"
        )
        code, _, err = self.run_main(self.args("gated-tool"))
        assert code == 1
        assert (
            "tests.guards.process still names tests/repo-record.test.ts, which the "
            "agent-rails branch deletes; fix the record"
        ) in err

    def test_exception_paths_and_approval_targets_are_renamed(self) -> None:
        found = draft(
            GATE,
            {
                "guardrails": {
                    "exceptions": [
                        {
                            "rule": "R",
                            "path": "agent/extensions/dynaflows/x.py",
                            "reason": "r",
                        }
                    ],
                    "coverage_ratchet": {
                        "approvals": [
                            {"path": "agent/skills/jev-actions/a.py", "approval": "ok"}
                        ]
                    },
                }
            },
        )
        cutover.apply_rewrite(found, cutover.AGENT_RAILS_REWRITE)
        assert found.exceptions[0]["path"] == "agent/extensions/agent-rails/x.py"
        assert (
            found.approvals[0]["target"]
            == "agent/extensions/agent-rails/skills/jev-actions/a.py"
        )
        assert found.approvals[0]["reason"] == "ok"
