"""Where this package sits in the harness (the oh-mi-pi-config checkout).

omp-guardrails runs from its skill folder in the harness (``uv run --project`` installs the
project editable), never from a repository's environment, so it reads harness files relative to
its own location. No home-directory or host path appears in code.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
# omp_guardrails/ -> python-reference/ -> assets/ -> setup-repository-guardrails/ -> skills/
SKILLS = PACKAGE.parents[3]
AGENT = SKILLS.parent
RAILS_CONFIG = AGENT / "agent-rails.yml"
PROFILE_TEMPLATE = SKILLS / "test-guidance/assets/templates/test-profile.md"
