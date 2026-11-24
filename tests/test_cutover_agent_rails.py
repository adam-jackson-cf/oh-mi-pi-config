"""scripts/cutover_agent_rails.py: the one-off conversion of repo records into config.json and of
the decision store to schema version 2.

Synthetic records under tests/fixtures/agentrails/records copy the profile template's sections and
the gate shapes of the four live records. git is replaced by a fake clone inspector here; the seam
tests drive real checkouts.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from collections.abc import Callable
from contextlib import closing
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
from guardrails.foundation import decision_store  # noqa: E402

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


# The version 1 decision-store.sql, comments dropped: the schema every live store is at before the cutover.
V1_SCHEMA = """
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS questions (
  judgment TEXT NOT NULL,
  question TEXT NOT NULL,
  question_version TEXT NOT NULL,
  approach TEXT NOT NULL CHECK (approach IN ('Gate', 'Route', 'Rerank', 'Select', 'Grade', 'Verify', 'Eval judge')),
  overlays TEXT NOT NULL CHECK (json_valid(overlays) AND json_type(overlays) = 'array'),
  primitive TEXT NOT NULL CHECK (primitive IN ('noul', 'choice', 'score')),
  evidence_unit TEXT NOT NULL CHECK (evidence_unit IN ('item', 'pair', 'placement')),
  combination TEXT NOT NULL CHECK (combination IN ('jev_only', 'proposer_product', 'chain_all', 'any', 'max_per_unit')),
  action TEXT NOT NULL CHECK (action IN ('block', 'confirm', 'review_queue', 'log', 'route')),
  policy_area TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('enforced', 'shadow')),
  decision TEXT NOT NULL CHECK (json_valid(decision)),
  precision_target REAL,
  override TEXT CHECK (override IS NULL OR json_valid(override)),
  shadow_exit TEXT CHECK (shadow_exit IS NULL OR json_valid(shadow_exit)),
  model TEXT NOT NULL,
  loaded_at TEXT NOT NULL,
  PRIMARY KEY (judgment, question, question_version)
);

CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  call_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('jev', 'check')),
  judgment TEXT NOT NULL,
  question TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('enforced', 'shadow')),
  status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'incomplete')),
  error TEXT,
  outcome TEXT CHECK (outcome IN ('flag', 'pass', 'escalate')),
  probability REAL,
  choice_probabilities TEXT CHECK (choice_probabilities IS NULL OR json_valid(choice_probabilities)),
  threshold REAL,
  proposer_score REAL,
  combined_score REAL,
  model TEXT,
  question_version TEXT,
  judgment_version TEXT,
  repo TEXT,
  commit_sha TEXT,
  pr INTEGER,
  trigger TEXT NOT NULL CHECK (trigger IN ('commit', 'prove', 'pr', 'manual')),
  item_kind TEXT,
  item TEXT,
  machine TEXT NOT NULL,
  state_sha256 TEXT,
  state_kept INTEGER NOT NULL DEFAULT 0 CHECK (state_kept IN (0, 1)),
  review_label TEXT CHECK (review_label IN ('0', '1', 'abstain')),
  review_rule TEXT,
  reviewed_at TEXT,
  review_batch TEXT,
  verdict TEXT CHECK (verdict IN ('true_flag', 'false_flag', 'missed', 'true_pass')),
  CHECK (status = 'ok' OR outcome IS NULL),
  CHECK (status != 'ok' OR outcome IS NOT NULL OR choice_probabilities IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS decisions_question ON decisions (judgment, question, outcome, ts);
CREATE INDEX IF NOT EXISTS decisions_review ON decisions (mode, review_label);
CREATE INDEX IF NOT EXISTS decisions_commit ON decisions (repo, commit_sha);
CREATE INDEX IF NOT EXISTS decisions_status ON decisions (status);
CREATE INDEX IF NOT EXISTS decisions_item ON decisions (item);
CREATE INDEX IF NOT EXISTS decisions_source ON decisions (source);
CREATE INDEX IF NOT EXISTS decisions_call ON decisions (call_id);

CREATE TABLE IF NOT EXISTS payloads (
  call_id TEXT PRIMARY KEY,
  state_sha256 TEXT NOT NULL,
  body BLOB NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS payloads_state ON payloads (state_sha256);

CREATE TABLE IF NOT EXISTS reviews (
  id INTEGER PRIMARY KEY,
  decision_id TEXT NOT NULL REFERENCES decisions (id),
  label TEXT NOT NULL CHECK (label IN ('0', '1', 'abstain')),
  rule TEXT,
  reason TEXT,
  labeller TEXT NOT NULL CHECK (labeller IN ('council', 'arbiter')),
  batch TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_decision ON reviews (decision_id);

CREATE TABLE IF NOT EXISTS rail_events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  session TEXT NOT NULL,
  repo TEXT,
  mode TEXT CHECK (mode IN ('minimal', 'structured')),
  tool TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('block', 'context', 'resolved')),
  code TEXT NOT NULL,
  reason TEXT CHECK (reason IS NULL OR length(reason) <= 200),
  calls INTEGER CHECK (calls IS NULL OR calls >= 0),
  refusals INTEGER CHECK (refusals IS NULL OR refusals > 0),
  machine TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rail_events_code ON rail_events (code, ts);
CREATE INDEX IF NOT EXISTS rail_events_repo ON rail_events (repo, mode);

CREATE VIEW IF NOT EXISTS shadow_progress AS
SELECT
  q.judgment,
  q.question,
  q.question_version,
  q.shadow_exit,
  COUNT(d.id) AS decisions,
  COALESCE(SUM(d.outcome = 'flag'), 0) AS flags,
  COALESCE(SUM(d.outcome = 'flag' AND d.review_label IN ('0', '1')), 0) AS labelled_flags,
  COALESCE(SUM(d.verdict = 'true_flag'), 0) AS true_flags,
  CAST(SUM(d.verdict = 'true_flag') AS REAL)
    / NULLIF(SUM(d.outcome = 'flag' AND d.review_label IN ('0', '1')), 0) AS precision,
  COALESCE(SUM(d.outcome = 'flag' AND d.review_label IN ('0', '1')), 0)
    >= json_extract(q.shadow_exit, '$.labelled_flags') AS exit_met
FROM questions AS q
LEFT JOIN decisions AS d
  ON d.source = 'jev'
  AND d.mode = 'shadow'
  AND d.judgment = q.judgment
  AND d.question = q.question
  AND d.question_version = q.question_version
WHERE q.mode = 'shadow'
GROUP BY q.judgment, q.question, q.question_version;

PRAGMA user_version = 1;
COMMIT;
"""
V1_RAIL_EVENTS = [
    (
        "2026-11-20T10:00:00Z",
        "s1",
        "owner/a",
        "structured",
        "bash",
        "block",
        "read-gate",
        "r",
        None,
        None,
        "mac",
    ),
    (
        "2026-11-20T10:01:00Z",
        "s1",
        "owner/a",
        "structured",
        "bash",
        "resolved",
        "read-gate",
        None,
        2,
        1,
        "mac",
    ),
    (
        "2026-11-20T10:02:00Z",
        "s2",
        None,
        None,
        "edit",
        "context",
        "path-check",
        "p",
        None,
        None,
        "nuc",
    ),
    (
        "2026-11-20T10:03:00Z",
        "s2",
        "owner/b",
        "minimal",
        "bash",
        "block",
        "minimal-merge",
        "m",
        None,
        None,
        "nuc",
    ),
]
V1_COUNTS = {
    "questions": 1,
    "decisions": 1,
    "payloads": 1,
    "reviews": 1,
    "rail_events": 4,
}
REAL_HOLDERS = cutover.holders
REAL_OMP_SESSIONS = cutover.omp_sessions


def seed_v1(connection: sqlite3.Connection) -> None:
    connection.executescript(V1_SCHEMA)
    connection.execute(
        "INSERT INTO questions (judgment, question, question_version, approach, overlays, primitive, "
        "evidence_unit, combination, action, policy_area, mode, decision, model, loaded_at) "
        "VALUES ('j', 'q', 'v1', 'Gate', '[]', 'noul', 'item', 'jev_only', 'block', 'area', 'shadow', "
        "'{}', 'model', '2026-11-20')"
    )
    connection.execute(
        "INSERT INTO decisions (id, call_id, ts, source, judgment, question, mode, status, outcome, "
        "trigger, machine, question_version) VALUES ('d1', 'c1', '2026-11-20', 'jev', 'j', 'q', "
        "'shadow', 'ok', 'flag', 'commit', 'mac', 'v1')"
    )
    connection.execute(
        "INSERT INTO payloads (call_id, state_sha256, body, created_at) "
        "VALUES ('c1', 'sha', x'00', '2026-11-20')"
    )
    connection.execute(
        "INSERT INTO reviews (decision_id, label, labeller, batch, ts) "
        "VALUES ('d1', '1', 'council', 'b1', '2026-11-20')"
    )
    connection.executemany(
        "INSERT INTO rail_events (ts, session, repo, mode, tool, kind, code, reason, calls, refusals, "
        "machine) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        V1_RAIL_EVENTS,
    )


def digests(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
    }


class StoreMigration(unittest.TestCase):
    """The store subcommand on a version 1 store copied, like the live one, with its WAL un-checkpointed."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp)
        self.live = self.temp / "live"
        self.live.mkdir()
        self.store = self.live / "dynaflows.db"
        self.backup = self.temp / "backup"
        writer = self.temp / "writer"
        writer.mkdir()
        with closing(
            sqlite3.connect(writer / "dynaflows.db", isolation_level=None)
        ) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA wal_autocheckpoint = 0")
            seed_v1(connection)
            for path in writer.iterdir():
                shutil.copy2(path, self.live / path.name)
        self.held: list[str] = []
        self.sessions: list[str] = []
        for name, value in (
            ("holders", lambda store: self.held),
            ("omp_sessions", lambda: self.sessions),
        ):
            patcher = patch.object(cutover, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_store(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cutover.main([
                "store",
                "--store",
                str(self.store),
                "--backup",
                str(self.backup),
                *extra,
            ])
        return code, out.getvalue(), err.getvalue()

    def test_the_fixture_holds_rows_in_an_uncheckpointed_wal(self) -> None:
        assert (self.live / "dynaflows.db-wal").stat().st_size > 0

    def test_the_dry_run_reports_the_planned_counts_and_leaves_the_store_untouched(
        self,
    ) -> None:
        self.sessions = ["77 omp"]
        before = digests(self.live)
        code, out, err = self.run_store()
        assert code == 0, err
        assert re.search(r"^  rail_events +4 +4$", out, re.MULTILINE), out
        assert re.search(r"^  decisions +1 +1$", out, re.MULTILINE), out
        assert "integrity_check: ok" in out
        assert f"dry run: {self.store} untouched" in out
        assert digests(self.live) == before
        assert not self.backup.exists()

    def test_apply_backs_up_the_store_then_migrates_it_to_v2_with_equal_counts(
        self,
    ) -> None:
        code, out, err = self.run_store("--apply")
        assert code == 0, err
        [saved] = self.backup.iterdir()
        assert f"backed up {self.store} to {saved}" in out
        assert re.search(r"^  rail_events +4 +4$", out, re.MULTILINE), out
        with closing(sqlite3.connect(saved / "dynaflows.db")) as old:
            assert old.execute("PRAGMA user_version").fetchone()[0] == 1
            assert cutover.table_counts(old) == V1_COUNTS
        with decision_store.opened(self.store) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
            assert cutover.table_counts(connection) == V1_COUNTS
            columns = [
                row["name"]
                for row in connection.execute("PRAGMA table_info(rail_events)")
            ]
            assert "mode" not in columns
            rows = connection.execute(
                f"SELECT {', '.join(columns)} FROM rail_events ORDER BY id"
            ).fetchall()
            assert [tuple(row)[1:] for row in rows] == [
                (*event[:3], *event[4:]) for event in V1_RAIL_EVENTS
            ]
            assert [
                row["name"]
                for row in connection.execute("PRAGMA index_info(rail_events_repo)")
            ] == ["repo"]

    def test_a_version_1_store_is_refused_by_the_python_readers(self) -> None:
        for opener in (decision_store.connect, decision_store.connect_read_only):
            with (
                self.subTest(opener=opener.__name__),
                pytest.raises(
                    decision_store.StoreError,
                    match="schema version 1; this harness reads version 2 only",
                ),
            ):
                opener(self.store)

    def test_the_store_is_refused_while_held_open_or_while_omp_runs(self) -> None:
        before = digests(self.live)
        self.held = ["4242"]
        code, _, err = self.run_store()
        assert code == 1
        assert "processes 4242 hold" in err
        self.held = []
        self.sessions = ["77 /opt/homebrew/bin/omp"]
        code, _, err = self.run_store("--apply")
        assert code == 1
        assert "omp session running: 77 /opt/homebrew/bin/omp" in err
        assert digests(self.live) == before
        assert not self.backup.exists()

    def test_usage_and_store_refusals(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            usage = cutover.main(["store", "--store", str(self.store), "--apply"])
        assert usage == 2
        assert "--apply needs --backup DIR" in err.getvalue()
        missing = self.temp / "absent.db"
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            assert cutover.main(["store", "--store", str(missing)]) == 1
        assert f"no decision store at {missing}" in err.getvalue()

    def test_a_store_already_at_v2_is_refused_without_a_backup(self) -> None:
        current = self.temp / "current" / "dynaflows.db"
        with decision_store.opened(current):
            pass
        self.store = current
        for extra in ((), ("--apply",)):
            code, _, err = self.run_store(*extra)
            assert code == 1
            assert "schema version 2; this migration reads version 1 only" in err
        assert not self.backup.exists()

    def test_a_failed_check_rolls_the_whole_migration_back(self) -> None:
        counts = cutover.table_counts
        calls = []

        def drifting(connection: sqlite3.Connection) -> dict[str, int]:
            calls.append(1)
            found = counts(connection)
            return {**found, "rail_events": 0} if len(calls) == 2 else found

        cutover.checkpoint(self.store)
        with (
            patch.object(cutover, "table_counts", drifting),
            pytest.raises(
                cutover.CutoverError, match="rolled back: row counts changed"
            ),
        ):
            cutover.migrate_store(self.store)
        with closing(sqlite3.connect(self.store)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert "mode" in cutover._columns(connection, "rail_events")
            assert cutover.table_counts(connection) == V1_COUNTS

    def test_the_backup_never_reuses_a_directory(self) -> None:
        cutover.backup_store(self.store, self.backup, "2026-11-24T09:00:00Z")
        with pytest.raises(FileExistsError):
            cutover.backup_store(self.store, self.backup, "2026-11-24T09:00:00Z")


class ProcessProbes(unittest.TestCase):
    """holders reads lsof and omp_sessions reads ps; both refuse when the tool is missing or fails."""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp)
        self.store = self.temp / "dynaflows.db"
        self.store.write_bytes(b"")
        (self.temp / "dynaflows.db-wal").write_bytes(b"")
        self.argv: list[list[str]] = []

    def fake(
        self, code: int, stdout: str = "", missing: bool = False
    ) -> Callable[..., subprocess.CompletedProcess[str]]:
        def run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            self.argv.append(argv)
            if missing:
                raise FileNotFoundError(argv[0])
            return subprocess.CompletedProcess(argv, code, stdout, "boom")

        return run

    def test_lsof_lists_the_pids_holding_any_store_file(self) -> None:
        with patch.object(cutover, "run_text", self.fake(0, "456\n123\n456\n")):
            assert REAL_HOLDERS(self.store) == ["123", "456"]
        assert self.argv[0] == [
            "lsof",
            "-t",
            "--",
            str(self.store),
            str(self.temp / "dynaflows.db-wal"),
        ]
        with patch.object(cutover, "run_text", self.fake(1)):
            assert REAL_HOLDERS(self.store) == []
        for run, message in (
            (self.fake(2), "lsof failed: boom"),
            (self.fake(0, missing=True), "lsof is not installed"),
        ):
            with (
                patch.object(cutover, "run_text", run),
                pytest.raises(cutover.CutoverError, match=message),
            ):
                REAL_HOLDERS(self.store)

    def test_ps_lists_omp_sessions_but_not_omp_workers_or_other_commands(self) -> None:
        listing = (
            "  10 /opt/homebrew/Cellar/omp/19.0.1/bin/omp\n"
            "  11 /opt/homebrew/Cellar/omp/19.0.1/bin/omp __omp_worker_daemon_broker\n"
            "  12 /bin/zsh -c omp -p hi\n"
            "  13 omp -p --no-session hi\n"
            "  14 /usr/bin/python3 scripts/omp.py\n"
        )
        with patch.object(cutover, "run_text", self.fake(0, listing)):
            assert REAL_OMP_SESSIONS() == [
                "10 /opt/homebrew/Cellar/omp/19.0.1/bin/omp",
                "13 omp -p --no-session hi",
            ]
        for run, message in (
            (self.fake(1), "ps failed: boom"),
            (self.fake(0, missing=True), "ps is not installed"),
        ):
            with (
                patch.object(cutover, "run_text", run),
                pytest.raises(cutover.CutoverError, match=message),
            ):
                REAL_OMP_SESSIONS()
