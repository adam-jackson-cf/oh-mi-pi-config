"""Collect-only lists: the test IDs a tier's adapter listing wrote, one per line."""

from __future__ import annotations

from pathlib import Path


def read_ids(path: Path) -> list[str] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    lines = (line.strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]
