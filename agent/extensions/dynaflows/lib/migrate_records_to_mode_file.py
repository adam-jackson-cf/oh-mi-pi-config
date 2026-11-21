"""Move each record's dynaflows.mode into the extension's modes.json and strip it from gate.json.

Usage: python3 migrate_records_to_mode_file.py [--dry-run] [--config-root DIR] [--modes FILE]

Reads <config root>/repos/<owner>/<name>/gate.json. A record setting structured gets a
modes.json entry; minimal removes the entry, since an unlisted repository is minimal.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import repo_record  # noqa: E402

MODES = ("minimal", "structured")
SLUG = re.compile(r"[a-z0-9_.-]+/[a-z0-9_.-]+")
DEFAULT_MODES = Path(__file__).resolve().parents[1] / "modes.json"


class MigrationError(RuntimeError):
    pass


def read_modes(path: Path) -> dict[str, str]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MigrationError(f"{path}: {error}") from error
    repos = document.get("repos") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or set(document) != {"schemaVersion", "repos"}
        or document["schemaVersion"] != 1
        or not isinstance(repos, dict)
        or any(
            not SLUG.fullmatch(key) or value not in MODES
            for key, value in repos.items()
        )
    ):
        raise MigrationError(f"{path}: not a schemaVersion 1 mode file")
    return dict(repos)


def record_mode(gate_path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MigrationError(f"{gate_path}: {error}") from error
    if not isinstance(gate, dict):
        raise MigrationError(f"{gate_path}: gate.json is not an object")
    if "dynaflows" not in gate:
        return gate, None
    setting = gate["dynaflows"]
    if (
        not isinstance(setting, dict)
        or set(setting) != {"mode"}
        or setting["mode"] not in MODES
    ):
        raise MigrationError(
            f'{gate_path}: dynaflows must be {{"mode": "minimal"|"structured"}}'
        )
    return gate, str(setting["mode"])


def migrate(config_root: Path, modes_path: Path, dry_run: bool) -> list[str]:
    """Plan (and unless dry_run, apply) the move; returns one line per change."""
    modes = read_modes(modes_path)
    lines: list[str] = []
    writes: list[tuple[Path, dict[str, Any]]] = []
    for gate_path in sorted(
        (config_root / repo_record.RECORDS_DIR).glob(f"*/*/{repo_record.GATE_FILE}")
    ):
        slug = f"{gate_path.parent.parent.name}/{gate_path.parent.name}".lower()
        gate, mode = record_mode(gate_path)
        if mode is None:
            continue
        del gate["dynaflows"]
        writes.append((gate_path, gate))
        lines.append(f"{slug}: {mode}; strip dynaflows from {gate_path}")
        if mode == "structured":
            modes[slug] = mode
        else:
            modes.pop(slug, None)
        reason = repo_record.gate_reason(gate)
        if reason is not None:
            lines.append(f"{slug}: gate.json still refused after the move: {reason}")
    if dry_run:
        return lines
    for gate_path, gate in writes:
        gate_path.write_text(json.dumps(gate, indent=2) + "\n", encoding="utf-8")
    document = {"schemaVersion": 1, "repos": dict(sorted(modes.items()))}
    modes_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run", action="store_true", help="print the plan; write nothing"
    )
    parser.add_argument("--config-root", type=Path, default=repo_record.config_root())
    parser.add_argument("--modes", type=Path, default=DEFAULT_MODES)
    options = parser.parse_args(argv)
    try:
        lines = migrate(options.config_root, options.modes, options.dry_run)
    except MigrationError as error:
        print(f"migrate: {error}", file=sys.stderr)
        return 1
    for line in lines or ["no record sets dynaflows"]:
        print(("would: " if options.dry_run else "") + line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
