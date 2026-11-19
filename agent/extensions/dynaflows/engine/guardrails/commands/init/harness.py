from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[2]
AGENT = PACKAGE.parents[3]
SKILLS = AGENT / "skills"
RECORDS = AGENT.parent / "repos"
PROFILE_TEMPLATE = AGENT / "lib/record/profile.md"
