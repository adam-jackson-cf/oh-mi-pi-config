import json
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "agent/extensions/dynaflows/lib")
)

import migrate_records_to_mode_file as migration  # noqa: E402

pytestmark = pytest.mark.seam

ROOT = Path(__file__).resolve().parents[1]


class Migrate(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.modes = self.root / "modes.json"
        self.modes.write_text(
            json.dumps({"schemaVersion": 1, "repos": {"owner/listed": "structured"}})
        )

    def record(self, slug: str, gate: object) -> Path:
        path = self.root / "repos" / slug / "gate.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(gate))
        return path

    def test_moves_each_mode_and_strips_it_from_gate_json(self) -> None:
        structured = self.record(
            "owner/on",
            {
                "schemaVersion": 2,
                "checks": {"commit": ["true"]},
                "dynaflows": {"mode": "structured"},
            },
        )
        minimal = self.record(
            "owner/listed",
            {
                "schemaVersion": 2,
                "checks": {"commit": ["true"]},
                "dynaflows": {"mode": "minimal"},
            },
        )
        untouched = self.record(
            "owner/plain", {"schemaVersion": 2, "checks": {"commit": ["true"]}}
        )
        before = untouched.read_text()
        lines = migration.migrate(self.root, self.modes, dry_run=False)
        assert lines == [
            f"owner/listed: minimal; strip dynaflows from {minimal}",
            f"owner/on: structured; strip dynaflows from {structured}",
        ]
        assert json.loads(self.modes.read_text()) == {
            "schemaVersion": 1,
            "repos": {"owner/on": "structured"},
        }
        for path in (structured, minimal):
            assert "dynaflows" not in json.loads(path.read_text())
        assert untouched.read_text() == before
        assert migration.migrate(self.root, self.modes, dry_run=False) == []

    def test_dry_run_writes_nothing(self) -> None:
        gate = self.record(
            "owner/on", {"schemaVersion": 2, "dynaflows": {"mode": "structured"}}
        )
        before = (gate.read_text(), self.modes.read_text())
        assert (
            migration.main([
                "--dry-run",
                "--config-root",
                str(self.root),
                "--modes",
                str(self.modes),
            ])
            == 0
        )
        assert (gate.read_text(), self.modes.read_text()) == before

    def test_reports_a_gate_still_refused_after_the_move(self) -> None:
        self.record(
            "owner/on", {"schemaVersion": 2, "dynaflows": {"mode": "structured"}}
        )
        lines = migration.migrate(self.root, self.modes, dry_run=True)
        assert (
            lines[-1]
            == "owner/on: gate.json still refused after the move: checks must be an object with checks.commit"
        )

    def test_refuses_a_bad_mode_or_mode_file_and_writes_nothing(self) -> None:
        gate = self.record(
            "owner/on", {"schemaVersion": 2, "dynaflows": {"mode": "on"}}
        )
        with pytest.raises(migration.MigrationError, match="dynaflows must be"):
            migration.migrate(self.root, self.modes, dry_run=False)
        assert "dynaflows" in json.loads(gate.read_text())
        gate.write_text(
            json.dumps({"schemaVersion": 2, "dynaflows": {"mode": "structured"}})
        )
        for text in (
            "{",
            json.dumps({"schemaVersion": 2, "repos": {}}),
            json.dumps({"schemaVersion": 1, "repos": {"Owner/X": "structured"}}),
        ):
            self.modes.write_text(text)
            assert (
                migration.main([
                    "--config-root",
                    str(self.root),
                    "--modes",
                    str(self.modes),
                ])
                == 1
            )

    def test_defaults_are_the_live_config_root_and_the_tracked_mode_file(self) -> None:
        assert migration.DEFAULT_MODES == ROOT / "agent/extensions/dynaflows/modes.json"
        migration.read_modes(migration.DEFAULT_MODES)
