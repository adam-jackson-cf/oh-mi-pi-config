"""Where this package sits in the harness (the oh-mi-pi-config checkout).

omp-guardrails runs from its extension folder in the harness (``uv run --project`` installs the
project editable), never from a repository's environment, so it reads harness files relative to
its own location. No home-directory or host path appears in code.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
# omp_guardrails/ -> guardrails/ -> dynaflows/ -> extensions/ -> agent/
AGENT = PACKAGE.parents[3]
SKILLS = AGENT / "skills"
# Live per-repo records are personal harness config, never committed.
RECORDS = AGENT.parent / "repos"
PROFILE_TEMPLATE = AGENT / "lib/record/profile.md"
