from __future__ import annotations

from pathlib import Path
from typing import Any

from guardrails.commands.adapters.javascript.lint import (
    lint as shared_lint,
    runner_vocabulary,
)
from guardrails.foundation.findings import Finding

VITEST = runner_vocabulary("vitest")


def lint(repo: Path, config: dict[str, Any], paths: list[str]) -> list[Finding]:
    return shared_lint(repo, config, paths, VITEST)
